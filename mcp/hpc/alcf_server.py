# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""MCP server exposing ALCF IRI Facility API tools via FastMCP."""

import json
import logging
import os
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))  # mcp/ root for context_utils

from mcp.server.fastmcp import FastMCP

from alcf_iri_client import ALCFIRIClient
from context_utils import bounded_file_text, clamp_int
from shell_args import ensure_login_shell, parse_arguments

logger = logging.getLogger(__name__)

# .env file lives in the project root directory (parent of mcp/)
# Per-user .env resolution centralized in mcp/auth_env.py (Claude Code strips
# CLAUDE_* from project MCP server env; auth_env reads a non-CLAUDE var instead).
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth_env import (
    env_file as _auth_env_file,
    read_value as _read_env_value,
    read_token as _read_token,
    write_token_names as _write_token_names,
)
_ENV_FILE = _auth_env_file()
def _load_env() -> None:
    """Load .env file into os.environ (without overwriting existing non-empty vars)."""
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip optional 'export ' prefix
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        existing = os.environ.get(key, "")
        if key and (not existing or existing.startswith("${")):
            os.environ[key] = value


_load_env()

mcp = FastMCP("alcf-iri")
client = ALCFIRIClient()
# Always re-read tokens from the file on every request (never trust a value
# cached at startup). See _read_env_value.
client.token_provider = lambda: _read_token("alcf")
client.transfer_token_provider = lambda: _read_env_value("GLOBUS_TRANSFER_TOKEN")


def _update_env(key: str, value: str) -> None:
    """Update or add a key=value pair in the .env file."""
    lines = []
    found = False
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text().splitlines():
            if line.lstrip().startswith(f"export {key}=") or line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    _ENV_FILE.write_text("\n".join(lines) + "\n")


def _fmt(data) -> str:
    """Format API response data as indented JSON string."""
    return json.dumps(data, indent=2, default=str)


def _extract_file_content(task_result: dict | list | str) -> str:
    """Extract plain file content from an IRI task result.

    IRI filesystem view/head/tail results have the structure:
      {"output": {"content": "<text>", "content_type": "bytes", ...}}
    """
    if not isinstance(task_result, dict):
        return str(task_result)
    output = task_result.get("output", task_result)
    if isinstance(output, dict) and "content" in output:
        return str(output["content"])
    if isinstance(output, list):
        return "\n".join(str(item) for item in output)
    return str(output)


# ── Authentication ──────────────────────────────────────────────────────


@mcp.tool()
async def authenticate(token: str = "") -> str:
    """Store a Globus access token for authenticated ALCF IRI API calls.

    Args:
        token: A valid Globus OAuth2 access token for the ALCF IRI API.
               If empty, falls back to IRI_TOKEN_ALCF
               (re-read at call time). So a bare
               authenticate() succeeds whenever .env already has a fresh token.

    Call this before using tools that require authentication (account,
    compute, filesystem). Facility and status tools work without auth.
    """
    # Read the token straight from the file (os.environ may hold a stale value
    # cached at server start; _load_env never overwrites it).
    token = token or _read_token("alcf")
    if not token:
        names = " / ".join(_write_token_names("alcf"))
        return (
            f"Error: no token provided and none of {names} found in {_ENV_FILE}. "
            "Run `python scripts/auth/alcf_iri_token.py` to obtain one, then retry."
        )
    client.set_token(token)
    for _name in _write_token_names("alcf"):
        _update_env(_name, token)
    # Verify by fetching projects
    try:
        projects = await client.list_projects()
        count = len(projects) if isinstance(projects, list) else 0
        return f"Authenticated successfully. Token saved to {_ENV_FILE}. Found {count} project(s)."
    except Exception as exc:
        return f"Token stored and saved to {_ENV_FILE}, but verification failed: {exc}"


# ── Facility & Status (no auth required) ────────────────────────────────


@mcp.tool()
async def get_facility_info() -> str:
    """Get facility metadata and list of sites.

    Returns facility name, organization, support URL, and sites with
    their locations and available compute resources. No authentication required.
    """
    try:
        facility = await client.get_facility()
        sites = await client.list_sites()
        return _fmt({"facility": facility, "sites": sites})
    except Exception as exc:
        return f"Error fetching facility info: {exc}"


@mcp.tool()
async def list_resources() -> str:
    """List all compute resources and their current status.

    Returns resource names, types (compute, storage, etc.), and current
    status (up, down, degraded). No authentication required.
    """
    try:
        resources = await client.list_resources()
        return _fmt(resources)
    except Exception as exc:
        return f"Error listing resources: {exc}"


# Known probe paths for filesystem health checks.
# Eagle requires the /eagle/ mount prefix; bare /datascience/... returns 400.
_FS_PROBE: dict[str, tuple[str, str]] = {
    "eagle": ("1c3ad9d4-2e91-42bc-becb-72b1fde1235c", "/eagle/datascience/hzheng"),
    "home":  ("6115bd2c-957a-4543-abff-5fae52992ff2", "/home/hzheng"),
}

# Mount prefixes stripped when converting an IRI mount path to a Globus path.
_GLOBUS_MOUNT_PREFIXES = ("/lus/eagle", "/eagle", "/grand", "/flare", "/home")


def _to_globus_path(path: str) -> str:
    """Strip an IRI mount prefix to produce a Globus-style path.

    /home/foo → /foo   |   /eagle/datascience/foo → /datascience/foo
    """
    for prefix in _GLOBUS_MOUNT_PREFIXES:
        if path.startswith(prefix + "/") or path == prefix:
            return path[len(prefix):] or "/"
    return path


# Mount-path prefix -> IRI storage resource UUID. Lets filesystem tools infer
# resource_id from the path so callers don't have to look it up (the mount
# prefix already determines the resource: /eagle -> Eagle, /home -> Home).
# Longest prefixes first so /lus/eagle wins over /eagle.
_PATH_PREFIX_TO_RESOURCE: tuple[tuple[str, str], ...] = (
    ("/lus/eagle", "1c3ad9d4-2e91-42bc-becb-72b1fde1235c"),  # Eagle (Lustre alt mount)
    ("/eagle",     "1c3ad9d4-2e91-42bc-becb-72b1fde1235c"),  # Eagle
    ("/home",      "6115bd2c-957a-4543-abff-5fae52992ff2"),  # Home
)


def _resolve_resource_id(resource_id: str, path: str) -> str:
    """Return an explicit resource_id, or infer one from the path's mount prefix.

    Filesystem tools need a storage resource UUID, but the absolute IRI path
    already encodes it (/eagle/... -> Eagle, /home/... -> Home). Callers may
    therefore omit resource_id and pass just the path. Returns "" only when the
    prefix is unrecognized and no explicit id was given.
    """
    if resource_id:
        return resource_id
    p = (path or "").strip()
    for prefix, rid in _PATH_PREFIX_TO_RESOURCE:
        if p == prefix or p.startswith(prefix + "/"):
            return rid
    return ""


_RESOURCE_HINT = (
    "resource_id is required but could not be inferred from the path. "
    "Pass a /eagle/... or /home/... absolute path, or set resource_id explicitly "
    "(Eagle=1c3ad9d4-2e91-42bc-becb-72b1fde1235c, Home=6115bd2c-957a-4543-abff-5fae52992ff2)."
)


async def _probe_filesystem(resource_id: str, probe_path: str) -> dict:
    """Probe a storage resource with IRI fs_ls, falling back to globus_ls.

    IRI's fs_ls goes through Globus Compute on the storage resource; that path
    breaks when the per-user GC daemon lock file is stale (LockFailed) and
    surfaces as HTTP 500. The Globus Transfer ls path is independent and
    almost always works when the underlying filesystem is up, so we use it as
    the fallback verdict.

    Returns a dict with: status (up/down/error), probe_path, method, latency_s,
    entries_visible, and (when fallback is used) iri_error.
    """
    import time
    from alcf_iri_client import IRI_RESOURCE_TO_GLOBUS

    iri_error: str | None = None
    t0 = time.monotonic()
    try:
        task_resp = await client.fs_ls(resource_id, probe_path)
        task_id = task_resp.get("task_id") or task_resp.get("id")
        if not task_id:
            iri_error = f"No task_id in response: {task_resp}"
        else:
            task = await client.wait_for_task(task_id)
            elapsed = round(time.monotonic() - t0, 2)
            status = task.get("status", "unknown")
            entries = task.get("result", {}).get("output", [])
            if status == "completed":
                return {
                    "status": "up",
                    "probe_path": probe_path,
                    "method": "fs_ls",
                    "entries_visible": len(entries),
                    "latency_s": elapsed,
                }
            iri_error = f"task_status={status}: {task.get('result', task)}"
    except Exception as exc:
        iri_error = str(exc)

    # IRI path failed — try Globus Transfer ls
    ep_id = IRI_RESOURCE_TO_GLOBUS.get(resource_id)
    if not ep_id:
        return {
            "status": "error",
            "probe_path": probe_path,
            "method": "fs_ls",
            "iri_error": iri_error,
            "detail": f"No Globus fallback mapped for resource {resource_id}",
        }

    logger.info("IRI fs_ls failed (%s); falling back to globus_ls on %s", iri_error, ep_id)
    globus_path = _to_globus_path(probe_path)
    t1 = time.monotonic()
    try:
        result = await client.globus_ls(ep_id, globus_path)
        elapsed = round(time.monotonic() - t1, 2)
        entries = result.get("DATA", []) if isinstance(result, dict) else []
        return {
            "status": "up",
            "probe_path": probe_path,
            "method": "globus_ls",
            "globus_path": globus_path,
            "entries_visible": len(entries),
            "latency_s": elapsed,
            "iri_error": iri_error,
        }
    except Exception as gexc:
        return {
            "status": "down",
            "probe_path": probe_path,
            "method": "globus_ls",
            "globus_path": globus_path,
            "iri_error": iri_error,
            "globus_error": str(gexc),
        }


@mcp.tool()
async def check_filesystem_health(system_name: str = "all") -> str:
    """Probe Eagle and/or Home filesystems to verify they are actually accessible.

    The IRI status API always reports storage resources as 'unknown'. This tool
    performs a real ls against a known path to confirm the filesystem is up.
    Tries the IRI fs_ls path first; on failure, falls back to a Globus Transfer
    ls (independent of the per-user Globus Compute daemon, which is a frequent
    source of HTTP 500s for the IRI path).

    Args:
        system_name: "eagle", "home", or "all" (default) to check both.

    Returns a per-filesystem verdict (up / down) with entry count, latency, and
    which method produced the verdict. Requires authentication — call
    authenticate() and authenticate_globus_transfer() first.
    """
    targets = (
        list(_FS_PROBE.items())
        if system_name.lower() == "all"
        else [(system_name.lower(), _FS_PROBE.get(system_name.lower(), ("", "")))]
    )

    results = {}
    for name, (resource_id, probe_path) in targets:
        if not resource_id:
            results[name] = {
                "status": "error",
                "detail": f"Unknown filesystem '{name}'. Use 'eagle', 'home', or 'all'.",
            }
            continue
        results[name] = await _probe_filesystem(resource_id, probe_path)

    return _fmt(results)


@mcp.tool()
async def get_system_status(system_name: str) -> str:
    """Get the current status of a specific ALCF system by name.

    Args:
        system_name: System name, e.g. "polaris", "aurora", "sophia", "crux",
                     "eagle", "home". Case-insensitive.

    Returns the matching resource entry with id, description, and current_status.
    For storage resources (eagle, home) the IRI always returns 'unknown'; this
    tool automatically falls back to a live Globus filesystem probe in that case.
    No authentication required for compute systems; storage probes require auth.
    """
    import json as _json
    try:
        resources = await client.list_resources()
        name_lower = system_name.lower()
        resource = None
        for r in resources:
            if r.get("name", "").lower() == name_lower:
                resource = r
                break

        if resource is None:
            names = [r.get("name") for r in resources]
            return f"System '{system_name}' not found. Available: {names}"

        # IRI reports 'unknown' for storage resources — fall back to live probe.
        # Uses _probe_filesystem which tries IRI fs_ls first, then globus_ls.
        if resource.get("current_status") == "unknown" and name_lower in _FS_PROBE:
            resource_id, probe_path = _FS_PROBE[name_lower]
            probe = await _probe_filesystem(resource_id, probe_path)
            resource["current_status"] = probe["status"] if probe["status"] in ("up", "down") else "unknown"
            probe["note"] = "IRI always returns unknown for storage; verdict from live ls probe"
            resource["filesystem_probe"] = probe

        return _fmt(resource)
    except Exception as exc:
        return f"Error fetching system status: {exc}"


@mcp.tool()
async def list_incidents(resource_id: str = "") -> str:
    """List active and recent incidents at the facility.

    Args:
        resource_id: Optional resource ID to filter incidents for a specific system.

    Returns incident details including type (planned/unplanned), status,
    affected resources, and timeline. No authentication required.
    """
    try:
        incidents = await client.list_incidents(resource_id or None)
        if not incidents:
            return "No incidents found."
        # Filter to active/recent (non-'down' status or most recent 10)
        active_statuses = {"active", "investigating", "monitoring", "identified", "watching"}
        active = [i for i in incidents if i.get("status", "").lower() in active_statuses]
        if active:
            return _fmt(active)
        # No active incidents — return the 5 most recent
        recent = sorted(incidents, key=lambda i: i.get("start", ""), reverse=True)[:5]
        return _fmt({"message": "No active incidents. Most recent 5:", "incidents": recent})
    except Exception as exc:
        return f"Error listing incidents: {exc}"


# ── Account ─────────────────────────────────────────────────────────────


@mcp.tool()
async def list_projects() -> str:
    """List all projects accessible to the authenticated user.

    Returns project names, IDs, descriptions, and member lists.
    Requires authentication — call authenticate() first.
    """
    try:
        projects = await client.list_projects()
        if not projects:
            return "No projects found for this user."
        return _fmt(projects)
    except Exception as exc:
        return f"Error listing projects: {exc}"


@mcp.tool()
async def get_project_allocations(project_id: str) -> str:
    """Get allocation details for a specific project.

    Args:
        project_id: The project ID to look up allocations for.

    Returns allocation entries with usage, limits, and associated
    compute capabilities. Requires authentication.
    """
    try:
        allocations = await client.get_project_allocations(project_id)
        if not allocations:
            return f"No allocations found for project '{project_id}'."
        return _fmt(allocations)
    except Exception as exc:
        return f"Error fetching allocations: {exc}"


# ── Compute ─────────────────────────────────────────────────────────────


@mcp.tool()
async def submit_job(
    resource_id: str,
    executable: str,
    arguments: str = "",
    name: str = "mcp-job",
    queue_name: str = "debug",
    account: str = "",
    duration: int = 300,
    node_count: int = 1,
    stdout_path: str = "",
    stderr_path: str = "",
    custom_attributes: str = "",
) -> str:
    """Submit a compute job to an HPC resource via the ALCF IRI API.

    Args:
        resource_id: The compute resource UUID (e.g., Polaris UUID).
        executable: The executable path (e.g., /bin/bash).
        arguments: Arguments for the executable, as either a JSON array
            (e.g. '["-lc", "mpiexec --cpu-bind list:0,1 ./a.out"]') or a
            comma-separated string. Prefer JSON when any argument contains
            a comma. For an inline shell command, '-lc,<commands>' keeps
            <commands> intact -- it is NOT split on its commas.
        name: Job name for tracking.
        queue_name: Scheduler queue name (e.g., debug, prod).
        account: Project/allocation account name for billing.
        duration: Wall-clock time limit in seconds. Minimum 300 for Polaris debug queue.
        node_count: Number of nodes to request.
        stdout_path: Path for stdout output file. Defaults to /home/hzheng/<name>.out.
                     NOTE: stdout_path is required by the ALCF IRI API.
        stderr_path: Path for stderr output file. Defaults to /home/hzheng/<name>.err.
        custom_attributes: JSON string of custom scheduler attributes (e.g., '{"filesystems": "home:eagle"}').

    Requires authentication. Returns job ID and initial status.
    """
    # Keeps a `-lc,<command>` command string verbatim instead of splitting it on
    # every comma, which both mangled the command and overran the API's
    # two-argument cap. A JSON array is also accepted; see shell_args.
    args_list = parse_arguments(executable, arguments)

    # Force a LOGIN shell for bash/sh invocations; see shell_args for why.
    # Folds into an existing flag group (`-c cmd` -> `-lc cmd`) so the
    # documented `["-lc", "<commands>"]` form stays at the two arguments the
    # ALCF API allows for /bin/bash.
    args_list = ensure_login_shell(executable, args_list)

    # stdout_path is mandatory in the ALCF IRI API
    resolved_stdout = stdout_path or f"/home/hzheng/{name}.out"
    resolved_stderr = stderr_path or f"/home/hzheng/{name}.err"

    job_spec: dict = {
        "executable": executable,
        "arguments": args_list,
        "name": name,
        "resources": {"node_count": node_count},
        "attributes": {
            "duration": duration,
            "queue_name": queue_name,
        },
        "stdout_path": resolved_stdout,
        "stderr_path": resolved_stderr,
    }

    if account:
        job_spec["attributes"]["account"] = account

    # ALCF IRI requires filesystems to be set; default to home:eagle if not specified
    if custom_attributes:
        try:
            job_spec["attributes"]["custom_attributes"] = json.loads(custom_attributes)
        except json.JSONDecodeError:
            return f"Invalid JSON in custom_attributes: {custom_attributes}"
    else:
        job_spec["attributes"]["custom_attributes"] = {"filesystems": "home:eagle"}

    try:
        result = await client.submit_job(resource_id, job_spec)
        summary = {
            "job_id": result.get("id"),
            "state": result.get("status", {}).get("state"),
            "system": "Polaris" if resource_id == "55c1c993-1124-47f9-b823-514ba3849a9a" else resource_id,
            "account": job_spec["attributes"].get("account", ""),
            "queue": job_spec["attributes"].get("queue_name", ""),
            "nodes": job_spec["resources"].get("node_count", 1),
            "duration_s": job_spec["attributes"].get("duration", ""),
            "executable": f"{job_spec.get('executable', '')} {' '.join(job_spec.get('arguments', []))}".strip(),
            "stdout": job_spec.get("stdout_path", ""),
            "stderr": job_spec.get("stderr_path", ""),
            "custom_attributes": job_spec["attributes"].get("custom_attributes", {}),
        }
        return _fmt(summary)
    except Exception as exc:
        return f"Error submitting job: {exc}"


@mcp.tool()
async def get_job_status(resource_id: str, job_id: str) -> str:
    """Get the status of a specific compute job.

    Args:
        resource_id: The compute resource UUID where the job was submitted.
        job_id: The job ID returned by submit_job.

    Returns job state (queued, active, completed, failed, canceled),
    timing info, and exit code if completed. Requires authentication.
    """
    try:
        result = await client.get_job_status(resource_id, job_id)
        return _fmt(result)
    except Exception as exc:
        return f"Error fetching job status: {exc}"


@mcp.tool()
async def list_jobs(resource_id: str) -> str:
    """List all jobs on a compute resource for the authenticated user.

    Args:
        resource_id: The compute resource UUID.

    Returns a list of jobs with their IDs, states, and specs.
    Requires authentication.
    """
    try:
        result = await client.list_jobs(resource_id)
        return _fmt(result)
    except Exception as exc:
        return f"Error listing jobs: {exc}"


@mcp.tool()
async def cancel_job(resource_id: str, job_id: str) -> str:
    """Cancel a running or queued compute job.

    Args:
        resource_id: The compute resource UUID.
        job_id: The job ID to cancel.

    Returns confirmation of cancellation. Requires authentication.
    """
    try:
        status_code = await client.cancel_job(resource_id, job_id)
        if status_code == 204:
            return f"Job '{job_id}' cancelled successfully."
        return f"Cancel request returned status {status_code}."
    except Exception as exc:
        return f"Error cancelling job: {exc}"


# ── Filesystem ──────────────────────────────────────────────────────────


@mcp.tool()
async def list_directory(path: str, resource_id: str = "", show_hidden: bool = False) -> str:
    """List contents of a remote directory on an HPC filesystem.

    Args:
        path: Absolute path prefixed with mount point — /eagle/... for Eagle,
              /home/... for Home, /lus/eagle/... for Eagle (Lustre mount).
              Do NOT use bare Globus paths (/datascience/...).
        resource_id: The storage resource UUID. OPTIONAL — inferred from the
              path's mount prefix (/eagle -> Eagle, /home -> Home) when omitted.
              Only pass it for paths whose prefix isn't recognized.
        show_hidden: Whether to include hidden files (dotfiles).

    This is an async operation — the tool submits the request and waits
    for the result. Requires authentication.
    """
    resource_id = _resolve_resource_id(resource_id, path)
    if not resource_id:
        return _fmt({"error": _RESOURCE_HINT, "path": path})
    iri_error = None
    try:
        result = await client.fs_ls(resource_id, path, show_hidden)
        if isinstance(result, dict) and "task_id" in result:
            task = await client.wait_for_task(result["task_id"])
            return _fmt(task.get("result", task))
        return _fmt(result)
    except Exception as iri_exc:
        iri_error = str(iri_exc)
        logger.info("IRI list_directory failed (%s), falling back to globus_ls", iri_error)

    # Strip mount prefix to get the Globus-style path
    # /home/foo → /foo   |   /eagle/datascience/foo → /datascience/foo
    from alcf_iri_client import IRI_RESOURCE_TO_GLOBUS
    ep_id = IRI_RESOURCE_TO_GLOBUS.get(resource_id)
    if not ep_id:
        return f"Error listing directory: IRI failed ({iri_error}), no Globus fallback for resource {resource_id}"

    globus_path = path
    for prefix in ("/home", "/eagle", "/lus/eagle", "/grand", "/flare"):
        if path.startswith(prefix + "/") or path == prefix:
            globus_path = path[len(prefix):] or "/"
            break

    try:
        result = await client.globus_ls(ep_id, globus_path, show_hidden)
        entries = result.get("DATA", [])
        simplified = [
            {"name": e.get("name"), "type": e.get("type"),
             "size": e.get("size"), "last_modified": e.get("last_modified")}
            for e in entries
        ]
        return _fmt({"path": path, "endpoint": ep_id, "entries": simplified})
    except Exception as globus_exc:
        return f"Error listing directory: IRI failed ({iri_error}), Globus also failed ({globus_exc})"


@mcp.tool()
async def read_file(
    path: str,
    resource_id: str = "",
    mode: str = "head",
    lines: int = 50,
    max_chars: int = 20000,
    full: bool = False,
) -> str:
    """Read content from a remote file on an HPC filesystem.

    Args:
        path: Absolute path prefixed with mount point — /eagle/... for Eagle,
              /home/... for Home. Do NOT use bare Globus paths.
        resource_id: The storage resource UUID. OPTIONAL — inferred from the
              path's mount prefix (/eagle -> Eagle, /home -> Home) when omitted.
        mode: How to read the file — 'head' (first N lines), 'tail' (last N lines), or 'view'.
        lines: Number of lines for head/tail mode. Clamped to 1..200.
        max_chars: Maximum characters to return (default 20000). Use 0 for no character cap.
        full: If true, return full content and skip head/tail selection. Use sparingly.

    This is an async operation. Requires authentication.
    """
    resource_id = _resolve_resource_id(resource_id, path)
    if not resource_id:
        return _fmt({"error": _RESOURCE_HINT, "path": path})
    lines = clamp_int(lines, minimum=1, maximum=200)
    iri_error = None
    try:
        if mode == "tail":
            result = await client.fs_tail(resource_id, path, lines)
        elif mode == "view":
            result = await client.fs_view(resource_id, path)
        else:
            result = await client.fs_head(resource_id, path, lines)

        if isinstance(result, dict) and "task_id" in result:
            task = await client.wait_for_task(result["task_id"])
            text = _extract_file_content(task.get("result", task))
        else:
            text = _extract_file_content(result)
        return bounded_file_text(
            text, path=path, mode=mode, lines=lines, max_chars=max_chars, full=full
        )
    except Exception as iri_exc:
        iri_error = str(iri_exc)
        logger.info("IRI read_file (%s) failed (%s); trying view then Globus", mode, iri_error)
        # Some IRI filesystem endpoints return HTTP 501 (e.g. /filesystem/tail is not
        # implemented) — that blocks reading job stdout/stderr to diagnose a failure. Try
        # `view` (whole-file, the most-likely-implemented mode) before the Globus fallback.
        if mode != "view":
            try:
                result = await client.fs_view(resource_id, path)
                if isinstance(result, dict) and "task_id" in result:
                    task = await client.wait_for_task(result["task_id"])
                    text = _extract_file_content(task.get("result", task))
                else:
                    text = _extract_file_content(result)
                return bounded_file_text(
                    text, path=path, mode="view", lines=lines, max_chars=max_chars, full=full
                )
            except Exception as view_exc:
                iri_error = f"{iri_error}; view fallback: {view_exc}"

    try:
        text = await client.globus_transfer_download(resource_id, path)
        return bounded_file_text(
            text, path=path, mode=mode, lines=lines, max_chars=max_chars, full=full
        )
    except Exception as globus_exc:
        return f"Error reading file: IRI failed ({iri_error}), Globus transfer also failed ({globus_exc})"


@mcp.tool()
async def upload_file(path: str, content: str, resource_id: str = "") -> str:
    """Upload a small file to the HPC filesystem.

    Args:
        path: Absolute destination path on the remote filesystem
              (/eagle/... or /home/...).
        content: The text content to upload.
        resource_id: The storage resource UUID. OPTIONAL — inferred from the
              path's mount prefix when omitted.

    Max file size is 5MB. Requires authentication.
    """
    resource_id = _resolve_resource_id(resource_id, path)
    if not resource_id:
        return _fmt({"error": _RESOURCE_HINT, "path": path})
    iri_error = None
    try:
        result = await client.fs_upload(resource_id, path, content)
        if isinstance(result, dict) and "task_id" in result:
            task = await client.wait_for_task(result["task_id"])
            return _fmt(task.get("result", task))
        return _fmt(result)
    except Exception as iri_exc:
        iri_error = str(iri_exc)
        logger.info("IRI upload failed (%s), falling back to Globus transfer", iri_error)

    try:
        result = await client.globus_transfer_upload(resource_id, path, content)
        return _fmt(result)
    except Exception as globus_exc:
        return f"Error uploading file: IRI failed ({iri_error}), Globus transfer also failed ({globus_exc})"


@mcp.tool()
async def download_file(
    path: str,
    resource_id: str = "",
    max_chars: int = 20000,
    full: bool = False,
) -> str:
    """Download a small file from the HPC filesystem.

    Args:
        path: Absolute path to the file to download (/eagle/... or /home/...).
        resource_id: The storage resource UUID. OPTIONAL — inferred from the
              path's mount prefix when omitted.
        max_chars: Maximum characters to return (default 20000). Use 0 for no character cap.
        full: If true, return full content. Use sparingly for large files.

    Returns a bounded preview by default. Requires authentication.
    """
    resource_id = _resolve_resource_id(resource_id, path)
    if not resource_id:
        return _fmt({"error": _RESOURCE_HINT, "path": path})
    iri_error = None
    try:
        result = await client.fs_download(resource_id, path)
        if isinstance(result, dict) and "task_id" in result:
            task = await client.wait_for_task(result["task_id"])
            text = _extract_file_content(task.get("result", task))
        else:
            text = _extract_file_content(result)
        return bounded_file_text(
            text, path=path, mode="view", lines=0, max_chars=max_chars, full=full
        )
    except Exception as iri_exc:
        iri_error = str(iri_exc)
        logger.info("IRI download failed (%s), falling back to Globus transfer", iri_error)

    try:
        text = await client.globus_transfer_download(resource_id, path)
        return bounded_file_text(
            text, path=path, mode="view", lines=0, max_chars=max_chars, full=full
        )
    except Exception as globus_exc:
        return f"Error downloading file: IRI failed ({iri_error}), Globus transfer also failed ({globus_exc})"


@mcp.tool()
async def get_task_status(task_id: str) -> str:
    """Check the status of an async filesystem task.

    Args:
        task_id: The task ID returned by a filesystem operation.

    Returns task status (pending, active, completed, failed, canceled)
    and result data if completed. Requires authentication.
    """
    try:
        task = await client.get_task(task_id)
        return _fmt(task)
    except Exception as exc:
        return f"Error fetching task status: {exc}"


# ── Globus Data Movement ───────────────────────────────────────────────


@mcp.tool()
async def authenticate_globus_transfer(token: str = "") -> str:
    """Store a Globus Transfer API token for data movement operations.

    Args:
        token: A Globus OAuth2 access token with the transfer scope
               (urn:globus:auth:scope:transfer.api.globus.org:all).
               If empty, falls back to GLOBUS_TRANSFER_TOKEN in .env
               (re-read at call time). So a bare
               authenticate_globus_transfer() succeeds whenever .env
               already has a fresh token.

    This is separate from the IRI API token. Required before using
    globus_transfer, globus_transfer_status, or globus_ls tools.
    """
    # Read straight from the file (os.environ may hold a stale startup value).
    token = token or _read_env_value("GLOBUS_TRANSFER_TOKEN")
    if not token:
        return (
            f"Error: no token provided and GLOBUS_TRANSFER_TOKEN not found in {_ENV_FILE}. "
            "Run `python scripts/auth/globus_auth.py ensure_valid` to mint one, then retry."
        )
    if len(token) < 60:
        return (
            f"ERROR: token appears truncated ({len(token)} chars). "
            "Globus tokens are 80-100+ chars. "
            "Get the full token with: python scripts/auth/globus_auth.py ensure_valid"
        )
    client.set_transfer_token(token)
    _update_env("GLOBUS_TRANSFER_TOKEN", token)
    try:
        # Verify by resolving a well-known endpoint
        ep_id = await client._resolve_endpoint("home")
        return f"Globus Transfer token stored and saved to {_ENV_FILE}. Verified: alcf#dtn_home -> {ep_id}"
    except Exception as exc:
        return f"Token stored and saved to {_ENV_FILE}, but verification failed: {exc}"


@mcp.tool()
async def local_endpoint() -> str:
    """Report this machine's Globus Connect Personal collection.

    Call this first when staging local files — it confirms Globus Connect
    Personal is set up and returns the collection UUID to pass as the
    `source_endpoint` of `globus_transfer`. If it returns an error, the user
    has not finished GCP setup and no upload will work until they do.

    The same collection ID is used for ALCF, NERSC, and OLCF: it identifies
    this machine, not the destination facility.
    """
    try:
        return _fmt({
            "collection_id": client._local_endpoint(),
            "note": (
                "Pass this UUID as source_endpoint to globus_transfer when "
                "uploading. Only the directories GCP publishes are reachable; "
                "use globus_ls to check a path before assuming it exists."
            ),
        })
    except Exception as exc:
        return f"Error resolving local Globus endpoint: {exc}"


@mcp.tool()
async def globus_ls(endpoint: str, path: str, show_hidden: bool = False) -> str:
    """List contents of a directory on a Globus endpoint.

    Args:
        endpoint: Globus endpoint — use a shorthand ("home", "eagle", "grand",
                  "flare"), a display name ("alcf#dtn_home"), or a UUID.
        path: Absolute path to list (e.g., "/hzheng" for home, "/eagle/MyProject").
        show_hidden: Whether to include hidden files.

    Common ALCF endpoints:
      - "home"  (alcf#dtn_home)  — path: /<username>
      - "eagle" (alcf#dtn_eagle) — path: /eagle/<project>
      - "grand" (alcf#dtn_grand) — path: /grand/<project>
      - "flare" (alcf#dtn_flare) — path: /<project>

    Requires authenticate_globus_transfer() first.
    """
    try:
        result = await client.globus_ls(endpoint, path, show_hidden)
        # Simplify output — extract file listing from Globus response
        entries = result.get("DATA", [])
        simplified = []
        for e in entries:
            simplified.append({
                "name": e.get("name"),
                "type": e.get("type"),
                "size": e.get("size"),
                "last_modified": e.get("last_modified"),
            })
        return _fmt({"path": path, "endpoint": endpoint, "entries": simplified})
    except Exception as exc:
        return f"Error listing directory: {exc}"


@mcp.tool()
async def globus_mkdir(endpoint: str, path: str, parents: bool = True) -> str:
    """Create a directory on a Globus endpoint before staging into it.

    Idempotent: a directory that already exists is reported, not an error. With
    `parents=True` (default) every missing ancestor is created top-down, so a
    full path like `/<project>/<your-username>/run1` is made in one call.

    Call this on the destination directory before `globus_transfer` so staging
    never fails on a missing folder. `endpoint` takes the same shorthand
    ("home", "eagle", "grand", "flare"), display name, or UUID as the other
    Globus tools. Requires authenticate_globus_transfer() first.
    """
    try:
        return _fmt(await client.globus_mkdir(endpoint, path, parents))
    except Exception as exc:
        return f"Error creating directory: {exc}"


@mcp.tool()
async def globus_transfer(
    source_endpoint: str,
    source_path: str,
    dest_endpoint: str,
    dest_path: str,
    label: str = "MCP transfer",
    recursive: bool = False,
) -> str:
    """Transfer a file or directory between Globus endpoints.

    Args:
        source_endpoint: Source endpoint — shorthand ("home", "eagle"),
                         display name ("alcf#dtn_home"), or UUID.
        source_path: Absolute path on the source endpoint.
        dest_endpoint: Destination endpoint (same format as source).
        dest_path: Absolute path on the destination endpoint.
        label: Human-readable label for the transfer task.
        recursive: Set to true to transfer a directory recursively.

    Use this to upload files to or download files from ALCF systems.

    Example — upload to home:
      source_endpoint="<your-local-endpoint-uuid>"
      source_path="/path/to/local/file.py"
      dest_endpoint="home"
      dest_path="/hzheng/file.py"

    Example — copy between ALCF filesystems:
      source_endpoint="home"
      source_path="/hzheng/script.py"
      dest_endpoint="eagle"
      dest_path="/eagle/MyProject/script.py"

    Requires authenticate_globus_transfer() first. Returns a task_id
    to track progress with globus_transfer_status().
    """
    try:
        result = await client.globus_transfer(
            source_endpoint, source_path,
            dest_endpoint, dest_path,
            label=label, recursive=recursive,
        )
        task_id = result.get("task_id", "unknown")
        return _fmt({
            "task_id": task_id,
            "message": result.get("message", "Transfer submitted"),
            "code": result.get("code", ""),
        })
    except Exception as exc:
        return f"Error submitting transfer: {exc}"


@mcp.tool()
async def globus_transfer_status(task_id: str) -> str:
    """Check the status of a Globus Transfer task.

    Args:
        task_id: The task ID returned by globus_transfer().

    Returns status (ACTIVE, SUCCEEDED, FAILED, INACTIVE),
    transfer progress, and error details if failed.
    Requires authenticate_globus_transfer() first.
    """
    try:
        task = await client.globus_get_task(task_id)
        return _fmt({
            "task_id": task.get("task_id"),
            "status": task.get("status"),
            "label": task.get("label"),
            "type": task.get("type"),
            "files": task.get("files", 0),
            "files_transferred": task.get("files_transferred", 0),
            "files_skipped": task.get("files_skipped", 0),
            "bytes_transferred": task.get("bytes_transferred", 0),
            "request_time": task.get("request_time"),
            "completion_time": task.get("completion_time"),
            "nice_status_details": task.get("nice_status_details"),
        })
    except Exception as exc:
        return f"Error fetching transfer status: {exc}"


# ── Entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
