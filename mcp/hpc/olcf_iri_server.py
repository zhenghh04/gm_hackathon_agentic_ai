# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""MCP server exposing OLCF IRI Facility API tools via FastMCP.

This server talks to the OLCF moderate-enclave IRI host (default:
amsc-moderate.s3m.olcf.ornl.gov), which serves Frontier. Despite the
hostname, the API is DOE IRI v0.4.3 — not the OPAT/Slurm S3M API used
by Odo (see mcp/olcf_s3m_server.py for that).

Authorization: the token's scope determines which tools work.
  * Facility + status (read-only): always available with any valid token.
  * Compute (submit/list/cancel) : requires the compute scope. Tokens
    issued without it return HTTP 401 on every /api/v1/compute/* path.
"""

import base64
import json
import logging
import os
import time
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))  # mcp/ root for context_utils

from mcp.server.fastmcp import FastMCP

from context_utils import bounded_file_text
from olcf_iri_client import OLCFIRIClient

logger = logging.getLogger(__name__)

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
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        existing = os.environ.get(key, "")
        if key and (not existing or existing.startswith("${")):
            os.environ[key] = value


_load_env()

mcp = FastMCP("olcf-iri")
client = OLCFIRIClient()
# Always re-read tokens from the file on every request (never trust a value
# cached at startup). See auth_env.read_value.
client.token_provider = lambda: _read_token("olcf")
client.transfer_token_provider = lambda: _read_env_value("GLOBUS_TRANSFER_TOKEN")


def _update_env(key: str, value: str) -> None:
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
    return json.dumps(data, indent=2, default=str)


def _is_shell_placeholder(token: str) -> bool:
    """True if token looks like an unresolved shell var, e.g. ${OLCF_IRI_TOKEN}."""
    t = (token or "").strip()
    return t.startswith("${") and t.endswith("}")


def _validate_jwt(token: str) -> tuple[bool, str]:
    """Local JWT sanity check (no network).

    Returns (ok, reason). ok=True when the token has 3 base64url segments,
    a decodable JSON payload, and an `exp` claim that is either absent or
    in the future. This is intentionally a *shape* check — it does not
    verify the signature or scope.
    """
    if not token or not token.strip():
        return False, "empty token"
    if _is_shell_placeholder(token):
        return False, (
            "token is an unresolved shell placeholder (e.g. ${OLCF_IRI_TOKEN}); "
            "pass the actual JWT value, not the variable reference"
        )
    parts = token.split(".")
    if len(parts) != 3:
        return False, f"not a 3-segment JWT (got {len(parts)} segment(s))"
    try:
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception as exc:
        return False, f"JWT payload not decodable: {exc}"
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        return False, f"JWT expired at unix time {int(exp)}"
    return True, "ok"


# ── Authentication ────────────────────────────────────────────────────


@mcp.tool()
async def authenticate(token: str = "") -> str:
    """Store an OLCF IRI access token for authenticated API calls.

    Args:
        token: A valid OLCF IRI Project Access Token (issued at
               https://my.olcf.ornl.gov → Projects → API Tokens).
               If empty, falls back to IRI_TOKEN_OLCF / OLCF_IRI_TOKEN in
               .env (re-read at call time). So a bare authenticate() succeeds
               whenever .env already has a fresh JWT.

    The token is bound to the host it was issued against
    (amsc-moderate.s3m.olcf.ornl.gov by default). For compute submission
    you need a token with the compute scope; status/facility endpoints
    work with any valid token.

    The tool refuses to store the value and leaves .env untouched if
    the token fails a local JWT shape check (wrong format, expired, or
    a literal shell placeholder like ${OLCF_IRI_TOKEN}). This guards
    against accidentally clobbering a working token in .env.

    Call this before using compute tools. Saved to .env as IRI_TOKEN_OLCF
    (and the legacy OLCF_IRI_TOKEN).
    """
    # Re-read .env in case the token was rotated after the server started.
    # OLCF tokens are manual-refresh only (myOLCF web UI); this just picks
    # up a freshly-pasted value without me having to grep .env.
    # Read straight from the file (os.environ may hold a stale startup value).
    token = token or _read_token("olcf")
    if not token:
        names = " / ".join(_write_token_names("olcf"))
        return (
            f"Error: no token provided and none of {names} found in {_ENV_FILE}. "
            "Issue one at https://my.olcf.ornl.gov (Projects → API Tokens, "
            "scope=compute), paste it into .env, then retry."
        )
    ok, reason = _validate_jwt(token)
    if not ok:
        return (
            f"Refusing to store token: {reason}. .env was NOT modified. "
            f"Issue a fresh token at https://my.olcf.ornl.gov "
            f"(Projects → API Tokens, scope=compute) and pass the literal JWT value."
        )
    client.set_token(token)
    for _name in _write_token_names("olcf"):
        _update_env(_name, token)
    try:
        f = await client.get_facility()
        return (
            f"Authenticated against {client.base_url}. "
            f"Facility: {f.get('short_name','?')} ({f.get('name','?')}). "
            f"Token saved to {_ENV_FILE}."
        )
    except Exception as exc:
        return f"Token stored and saved to {_ENV_FILE}, but verification failed: {exc}"


@mcp.tool()
async def authenticate_globus_transfer(token: str = "") -> str:
    """Store a Globus Transfer access token for OLCF data movement.

    Args:
        token: A Globus OAuth2 access token with the transfer scope
               (urn:globus:auth:scope:transfer.api.globus.org:all).
               If empty, falls back to GLOBUS_TRANSFER_TOKEN in .env
               (re-read at call time). So a bare
               authenticate_globus_transfer() succeeds whenever .env
               already has a fresh token.

    Refuses to overwrite GLOBUS_TRANSFER_TOKEN in .env if the value is an
    unresolved shell placeholder (e.g. ${GLOBUS_TRANSFER_TOKEN}).

    Required before using globus_transfer / globus_ls / upload_file /
    download_file. Separate from the OLCF IRI token.
    """
    # Read straight from the file (os.environ may hold a stale startup value).
    token = token or _read_env_value("GLOBUS_TRANSFER_TOKEN")
    if not token or not token.strip():
        return (
            f"Error: no token provided and GLOBUS_TRANSFER_TOKEN not found in {_ENV_FILE}. "
            "Run `python scripts/globus_auth.py ensure_valid` to mint one, then retry."
        )
    if _is_shell_placeholder(token):
        return (
            "Refusing to store token: value is an unresolved shell placeholder "
            f"({token!r}); pass the actual access-token value. .env was NOT modified."
        )
    client.set_transfer_token(token)
    _update_env("GLOBUS_TRANSFER_TOKEN", token)
    return f"Globus Transfer token stored and saved to {_ENV_FILE}."


# ── Facility ──────────────────────────────────────────────────────────


@mcp.tool()
async def get_facility_info() -> str:
    """Get OLCF facility metadata and list of sites.

    Returns facility name, organization, support URL, and sites with
    their locations.
    """
    try:
        facility = await client.get_facility()
        sites = await client.list_sites()
        return _fmt({"facility": facility, "sites": sites})
    except Exception as exc:
        return f"Error fetching facility info: {exc}"


# ── Status ────────────────────────────────────────────────────────────


@mcp.tool()
async def list_resources() -> str:
    """List all OLCF compute and storage resources with current status.

    Returns each resource's id, name, type, and current_status (up/down/etc).
    On the moderate enclave the only resource is currently Frontier.
    """
    try:
        return _fmt(await client.list_resources())
    except Exception as exc:
        return f"Error listing resources: {exc}"


@mcp.tool()
async def get_system_status(system_name: str) -> str:
    """Get the current status of a specific OLCF system by name.

    Args:
        system_name: System name, e.g. "frontier". Case-insensitive match
                     against the resource's `name` field.

    Returns the matching resource entry with id, description, and
    current_status. Useful before submitting a job.
    """
    try:
        resources = await client.list_resources()
        target = system_name.lower()
        for r in resources if isinstance(resources, list) else []:
            if r.get("name", "").lower() == target:
                return _fmt(r)
        return _fmt({"error": f"system '{system_name}' not found",
                     "available": [r.get("name") for r in resources]})
    except Exception as exc:
        return f"Error fetching status for {system_name}: {exc}"


@mcp.tool()
async def list_incidents(resource_id: str = "") -> str:
    """List active and recent incidents at OLCF.

    Args:
        resource_id: Optional resource UUID to filter to a specific system.

    Note: this endpoint may return 404 on the moderate enclave when no
    incident store is configured; treat 404 as "no incidents recorded".
    """
    try:
        return _fmt(await client.list_incidents(resource_id or None))
    except Exception as exc:
        return f"Error listing incidents: {exc}"


# ── Compute ───────────────────────────────────────────────────────────


@mcp.tool()
async def submit_job(
    resource_id: str,
    executable: str,
    arguments: str = "",
    name: str = "olcf-iri-job",
    queue_name: str = "batch",
    account: str = "",
    duration: int = 600,
    node_count: int = 1,
    process_count: int = 0,
    processes_per_node: int = 0,
    cpu_cores_per_process: int = 0,
    gpu_cores_per_process: int = 0,
    directory: str = "",
    stdout_path: str = "",
    stderr_path: str = "",
    reservation_id: str = "",
    custom_attributes: str = "",
) -> str:
    """Submit a job to an OLCF compute resource via IRI.

    Args:
        resource_id: Compute resource UUID. For Frontier on the moderate
                     enclave: 5173cdb4-d82f-5c81-8ef0-598997c12813.
        executable: Path to the executable (e.g. /bin/bash, /ccs/home/.../run.sh).
        arguments: Comma-separated arguments for the executable.
        name: Job name.
        queue_name: Slurm partition / queue (e.g. "batch", "extended").
        account: OLCF allocation account (e.g. "csc708").
        duration: Wall-clock limit in seconds. Frontier batch min ~60 s.
        node_count: Number of nodes to request.
        process_count: Total MPI tasks (optional; PSI/J derives if omitted).
        processes_per_node: MPI tasks per node (optional).
        cpu_cores_per_process: CPU cores per task (optional).
        gpu_cores_per_process: GPU cores per task; on Frontier set to 8 for
                               full-node GPU access (8 GCDs per node).
        directory: Working directory on the compute node.
        stdout_path / stderr_path: Output file paths on the OLCF filesystem.
        reservation_id: Optional Slurm reservation name.
        custom_attributes: JSON string of scheduler-specific extras
                          (e.g. '{"constraint": "nvme"}').

    Requires authentication AND a token with compute scope; otherwise the
    server returns HTTP 401. Returns the submitted Job object including its id.
    """
    try:
        custom: dict = {}
        if custom_attributes:
            try:
                custom = json.loads(custom_attributes)
            except json.JSONDecodeError as je:
                return f"Invalid custom_attributes JSON: {je}"

        args_list = [a.strip() for a in arguments.split(",") if a.strip()]
        # Force a LOGIN shell for bash/sh script invocations. IRI/PSI-J runs
        # `<executable> <args>` NON-login, bypassing the `#!/bin/bash -l` shebang ->
        # module/mpiexec missing -> exit 127. No-op when executable is the script
        # itself; idempotent if -l/--login is already present.
        if os.path.basename(executable) in ("bash", "sh") and not ({"-l", "--login"} & set(args_list)):
            args_list = ["-l"] + args_list

        spec = client.build_job_spec(
            executable=executable,
            arguments=args_list or None,
            name=name,
            directory=directory or None,
            stdout_path=stdout_path or None,
            stderr_path=stderr_path or None,
            node_count=node_count or None,
            process_count=process_count or None,
            processes_per_node=processes_per_node or None,
            cpu_cores_per_process=cpu_cores_per_process or None,
            gpu_cores_per_process=gpu_cores_per_process or None,
            duration=duration or None,
            queue_name=queue_name or None,
            account=account or None,
            reservation_id=reservation_id or None,
            custom_attributes=custom or None,
        )
        return _fmt(await client.submit_job(resource_id, spec))
    except Exception as exc:
        return f"Error submitting job: {exc}"


@mcp.tool()
async def get_job_status(resource_id: str, job_id: str) -> str:
    """Get the status of a specific OLCF compute job.

    Args:
        resource_id: Compute resource UUID where the job runs.
        job_id: Job ID returned by submit_job.

    Returns Job + JobStatus (state, time, exit_code if completed).
    Requires compute scope on the IRI token.
    """
    try:
        return _fmt(await client.get_job_status(resource_id, job_id))
    except Exception as exc:
        return f"Error fetching job status: {exc}"


@mcp.tool()
async def list_jobs(resource_id: str) -> str:
    """List jobs on an OLCF compute resource for the authenticated user.

    Args:
        resource_id: Compute resource UUID.

    Requires compute scope on the IRI token.
    """
    try:
        return _fmt(await client.list_jobs(resource_id))
    except Exception as exc:
        return f"Error listing jobs: {exc}"


@mcp.tool()
async def cancel_job(resource_id: str, job_id: str) -> str:
    """Cancel a queued or running OLCF compute job.

    Args:
        resource_id: Compute resource UUID.
        job_id: Job ID to cancel.

    Requires compute scope on the IRI token.
    """
    try:
        code = await client.cancel_job(resource_id, job_id)
        return _fmt({"status": "canceled", "http_status": code, "job_id": job_id})
    except Exception as exc:
        return f"Error canceling job: {exc}"


# ── Globus Transfer (filesystem ops) ──────────────────────────────────
# OLCF IRI on the moderate enclave does NOT expose a filesystem API.
# Use Globus to move files; the OLCF DTN endpoint (UUID
# 36d521b3-c182-4071-b7d5-91db5d380d42) covers /ccs/home and Orion scratch.


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
    """List a directory on a Globus endpoint.

    Args:
        endpoint: "olcf" / "frontier" / "orion" (all → OLCF DTN), or a UUID.
        path: Absolute path on the endpoint.
              Home:    /ccs/home/<user>
              Scratch: /lustre/orion/<project>/scratch/<user>
        show_hidden: Whether to include dotfiles.

    Requires authenticate_globus_transfer() first.
    """
    try:
        return _fmt(await client.globus_ls(endpoint, path, show_hidden))
    except Exception as exc:
        return f"Error listing {endpoint}:{path}: {exc}"


@mcp.tool()
async def globus_mkdir(endpoint: str, path: str, parents: bool = True) -> str:
    """Create a directory on a Globus endpoint before staging into it.

    Idempotent: a directory that already exists is reported, not an error. With
    `parents=True` (default) every missing ancestor is created top-down, so a
    full path like `/ccs/home/<your-username>/run1` (or an Orion scratch path)
    is made in one call.

    Call this on the destination directory before `globus_transfer` so staging
    never fails on a missing folder. `endpoint` takes the same shorthand,
    display name, or UUID as the other Globus tools. Requires
    authenticate_globus_transfer() first.
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
    label: str = "OLCF IRI MCP transfer",
    recursive: bool = False,
) -> str:
    """Transfer a file or directory between Globus endpoints.

    Args:
        source_endpoint / dest_endpoint: shorthand ("olcf"), display name,
                          or UUID. For local: your Globus Connect Personal UUID.
        source_path / dest_path: absolute paths on the respective endpoints.
        label: human-readable transfer label.
        recursive: True for directories.

    Returns a task_id usable with globus_transfer_status.
    Requires authenticate_globus_transfer() first.
    """
    try:
        result = await client.globus_transfer(
            source_endpoint, source_path, dest_endpoint, dest_path,
            label=label, recursive=recursive,
        )
        return _fmt(result)
    except Exception as exc:
        return f"Error submitting transfer: {exc}"


@mcp.tool()
async def globus_transfer_status(task_id: str) -> str:
    """Check the status of a Globus Transfer task.

    Args:
        task_id: Task ID returned by globus_transfer.

    Returns ACTIVE / SUCCEEDED / FAILED / INACTIVE plus progress info.
    """
    try:
        return _fmt(await client.globus_get_task(task_id))
    except Exception as exc:
        return f"Error fetching transfer status: {exc}"


@mcp.tool()
async def upload_file(remote_path: str, content: str, endpoint: str = "olcf") -> str:
    """Upload text content to a file on an OLCF filesystem via Globus.

    Args:
        remote_path: Absolute destination on OLCF (e.g. /ccs/home/<user>/run.sh
                     or /lustre/orion/<project>/scratch/<user>/run.sh).
        content: Text to write.
        endpoint: Globus endpoint shorthand ("olcf") or UUID.

    Writes content to a local temp file under ~/tmp/, transfers via Globus,
    then cleans up. Requires authenticate_globus_transfer() and a running
    local Globus Connect Personal endpoint.
    """
    try:
        return _fmt(await client.upload_file(remote_path, content, endpoint))
    except Exception as exc:
        return f"Error uploading {remote_path}: {exc}"


@mcp.tool()
async def download_file(
    remote_path: str,
    endpoint: str = "olcf",
    max_chars: int = 20000,
    full: bool = False,
) -> str:
    """Download bounded content from an OLCF filesystem via Globus.

    Args:
        remote_path: Absolute path on the OLCF filesystem.
        endpoint: Globus endpoint shorthand ("olcf") or UUID.
        max_chars: Maximum characters to return (default 20000). Use 0 for no character cap.
        full: If true, return full content. Use sparingly for large files.

    Returns bounded text by default. Larger exact transfers should use
    globus_transfer directly to a local path.
    """
    try:
        content = await client.download_file(remote_path, endpoint)
        return bounded_file_text(
            content, path=remote_path, mode="view", lines=0, max_chars=max_chars, full=full
        )
    except Exception as exc:
        return f"Error downloading {remote_path}: {exc}"


if __name__ == "__main__":
    mcp.run()
