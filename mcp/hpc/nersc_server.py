# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""MCP server exposing NERSC IRI API tools via FastMCP."""

import json
import logging
import os
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))  # mcp/ root for context_utils

from mcp.server.fastmcp import FastMCP

from context_utils import bounded_file_text, clamp_int
from nersc_iri_client import NERSCIRIClient

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

mcp = FastMCP("nersc-iri")
client = NERSCIRIClient()
# Always re-read tokens from the file on every request (never trust a value
# cached at startup). See auth_env.read_value.
client.token_provider = lambda: _read_token("nersc")
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


# ── Authentication ──────────────────────────────────────────────────────


@mcp.tool()
async def authenticate(token: str = "") -> str:
    """Store a NERSC IRI bearer token for authenticated calls.

    Args:
        token: A valid NERSC IRI Bearer token. Obtain one by running:
               python scripts/auth/nersc_iri_token.py
               If empty, falls back to IRI_TOKEN_NERSC / NERSC_IRI_TOKEN in
               .env (re-read at call time). So a bare authenticate() succeeds
               whenever .env already has a fresh token.

    Call this before using tools that require authentication.
    Status tools work without auth.
    """
    # Read straight from the file (os.environ may hold a stale startup value).
    token = token or _read_token("nersc")
    if not token:
        names = " / ".join(_write_token_names("nersc"))
        return (
            f"Error: no token provided and none of {names} found in {_ENV_FILE}. "
            "Run `python scripts/auth/nersc_iri_token.py` to obtain one, then retry."
        )
    client.set_token(token)
    for _name in _write_token_names("nersc"):
        _update_env(_name, token)
    try:
        account = await client.get_account()
        uids = account.get("user_ids") or []
        username = uids[0] if uids else account.get("username", "unknown")
        return f"Authenticated as '{username}'. Token saved to {_ENV_FILE}."
    except Exception as exc:
        return f"Token stored and saved to {_ENV_FILE}, but verification failed: {exc}"


@mcp.tool()
async def fetch_token() -> str:
    """Validate the stored NERSC IRI token from .env.

    NERSC IRI tokens are obtained interactively via
    `python scripts/auth/nersc_iri_token.py` (Globus OAuth2 flow). This tool
    just verifies that the token currently in IRI_TOKEN_NERSC (or the legacy
    NERSC_IRI_TOKEN) is still valid; it cannot mint a new one
    non-interactively.
    """
    existing = _read_token("nersc")
    if not existing:
        names = " / ".join(_write_token_names("nersc"))
        return (
            f"No NERSC IRI token in .env (looked for {names}). "
            "Run `python scripts/auth/nersc_iri_token.py` to obtain one."
        )
    client.set_token(existing)
    try:
        account = await client.get_account()
        uids = account.get("user_ids") or []
        username = uids[0] if uids else account.get("username", "unknown")
        return f"Using stored NERSC IRI token — authenticated as '{username}'."
    except Exception as exc:
        return (
            f"Stored NERSC IRI token present but verification failed: {exc}. "
            "Refresh with `python scripts/auth/nersc_iri_token.py`."
        )


# ── Status (no auth required) ────────────────────────────────────────────


@mcp.tool()
async def list_systems() -> str:
    """List all NERSC systems and their current status.

    Returns system names, availability, and notes. No authentication required.
    Common systems: perlmutter, dtns, hpss.
    """
    try:
        systems = await client.list_systems()
        return _fmt(systems)
    except Exception as exc:
        return f"Error listing systems: {exc}"


@mcp.tool()
async def get_system_status(system_name: str) -> str:
    """Get the current status of a specific NERSC system.

    Args:
        system_name: System name, e.g. "perlmutter", "dtns", "hpss".

    Returns availability status and any active notes. No authentication required.
    """
    try:
        status = await client.get_system_status(system_name)
        return _fmt(status)
    except Exception as exc:
        return f"Error fetching status for '{system_name}': {exc}"


# ── Account ─────────────────────────────────────────────────────────────


@mcp.tool()
async def get_account() -> str:
    """Get the current user's NERSC account information.

    Returns username, email, and account details. Requires authentication.
    """
    try:
        account = await client.get_account()
        return _fmt(account)
    except Exception as exc:
        return f"Error fetching account: {exc}"


@mcp.tool()
async def list_projects() -> str:
    """List all NERSC projects and allocations accessible to the authenticated user.

    Returns project names, IDs, and repo/allocation details.
    Requires authentication.
    """
    try:
        projects = await client.list_projects()
        if not projects:
            return "No projects found for this user."
        return _fmt(projects)
    except Exception as exc:
        return f"Error listing projects: {exc}"


# ── Compute ─────────────────────────────────────────────────────────────


@mcp.tool()
async def submit_job(
    system_name: str,
    script: str,
    is_path: bool = False,
) -> str:
    """Submit a Slurm job to a NERSC system.

    Args:
        system_name: Target system, e.g. "perlmutter".
        script: Either the full #SBATCH job script content (is_path=False),
                or an absolute path to a script file already on the system
                (is_path=True).
        is_path: If True, `script` is a remote file path. The client reads
                 the file and parses #SBATCH directives the same way as
                 inline content — bare `bash <path>` execution would silently
                 drop --account / --qos / --constraint / --time and land the
                 job on the user's default partition (CPU instead of GPU).

    Example script content:
        #!/bin/bash
        #SBATCH --account=m1234
        #SBATCH --qos=debug
        #SBATCH --constraint=gpu
        #SBATCH --nodes=1
        #SBATCH --time=00:10:00
        srun python train.py

    Returns job ID and initial status. Requires authentication.
    """
    try:
        result = await client.submit_job(system_name, script, is_path=is_path)
        # Parse #SBATCH directives for the summary report. With is_path=True
        # the client has already read+parsed the file for the actual
        # submission; for the summary we just skip the parse rather than
        # round-tripping to the filesystem a second time.
        import re as _re
        sbatch: dict = {}
        for line in (script.splitlines() if not is_path else []):
            m = _re.match(r"#SBATCH\s+--?([\w-]+)(?:[= ](.*))?", line.strip())
            if m:
                sbatch[m.group(1).replace("-", "_")] = (m.group(2) or "").strip().strip('"')
        summary = {
            "job_id": result.get("id"),
            "state": result.get("status", {}).get("state"),
            "system": system_name,
            "account": sbatch.get("account", sbatch.get("A", "")),
            "queue": sbatch.get("qos", sbatch.get("partition", sbatch.get("p", sbatch.get("q", "")))),
            "nodes": sbatch.get("nodes", sbatch.get("N", "")),
            "walltime": sbatch.get("time", ""),
            "constraint": sbatch.get("constraint", ""),
            "job_name": sbatch.get("job_name", sbatch.get("J", "")),
            "stdout": sbatch.get("output", sbatch.get("o", "")),
        }
        return _fmt(summary)
    except Exception as exc:
        return f"Error submitting job: {exc}"


@mcp.tool()
async def get_job_status(system_name: str, job_id: str) -> str:
    """Get the status of a specific Slurm job on a NERSC system.

    Args:
        system_name: System where the job was submitted, e.g. "perlmutter".
        job_id: The Slurm job ID returned by submit_job.

    Returns job state, queue, nodes, and timing. Requires authentication.
    """
    try:
        result = await client.get_job_status(system_name, job_id)
        status = result.get("status", {})
        meta = status.get("meta_data", {})
        summary: dict = {
            "job_id": result.get("id"),
            "state": status.get("state"),
            "slurm_state": meta.get("state", ""),
            "reason": meta.get("reason", ""),
            "job_name": meta.get("jobname", ""),
            "account": meta.get("account", ""),
            "partition": meta.get("partition", ""),
            "qos": meta.get("qos", ""),
            "nodes": meta.get("nnodes", ""),
            "nodelist": meta.get("nodelist", ""),
            "timelimit": meta.get("timelimit", ""),
            "elapsed": meta.get("elapsed", ""),
            "submit": meta.get("submit", ""),
            "start": meta.get("start", ""),
            "end": meta.get("end", ""),
            "exit_code": status.get("exit_code"),
            "workdir": meta.get("workdir", ""),
            "stdout": meta.get("stdout", "") or meta.get("stdoutpath", ""),
        }
        # Drop empty / unknown / None values
        summary = {k: v for k, v in summary.items()
                   if v is not None and v != "" and v != "Unknown" and v != "None"}
        return _fmt(summary)
    except Exception as exc:
        return f"Error fetching job status: {exc}"


@mcp.tool()
async def list_jobs(system_name: str) -> str:
    """List all jobs for the authenticated user on a NERSC system.

    Args:
        system_name: System to query, e.g. "perlmutter".

    Returns job IDs, states, names, and queue info. Requires authentication.
    """
    try:
        result = await client.list_jobs(system_name)
        return _fmt(result)
    except Exception as exc:
        return f"Error listing jobs: {exc}"


@mcp.tool()
async def cancel_job(system_name: str, job_id: str) -> str:
    """Cancel a running or queued Slurm job on a NERSC system.

    Args:
        system_name: System where the job is running, e.g. "perlmutter".
        job_id: The Slurm job ID to cancel.

    Returns confirmation of cancellation. Requires authentication.
    """
    try:
        result = await client.cancel_job(system_name, job_id)
        return _fmt(result)
    except Exception as exc:
        return f"Error cancelling job {job_id}: {exc}"


# ── Filesystem ──────────────────────────────────────────────────────────


@mcp.tool()
async def list_directory(
    system_name: str,
    path: str,
    max_entries: int = 200,
    name_contains: str = "",
) -> str:
    """List contents of a directory on a NERSC filesystem.

    Args:
        system_name: System to query, e.g. "perlmutter", "compute", "dtns".
        path: Absolute path to the directory, e.g. "/global/homes/h/hzheng".
        max_entries: Cap on returned entries (default 200). Home/scratch dirs
            can have thousands of files and overflow the agent context — this
            truncates after sorting (dirs first, then files alphabetically).
            Use 0 for unlimited.
        name_contains: Case-insensitive substring filter on entry names.
            Empty returns all (subject to max_entries).

    Common paths:
      - Home:    /global/homes/<initial>/<username>
      - Scratch: /pscratch/sd/<initial>/<username>
      - CFS:     /global/cfs/cdirs/<project>

    Requires authentication.
    """
    try:
        result = await client.fs_ls(system_name, path)
        entries = result.get("output") if isinstance(result, dict) else result
        if isinstance(entries, list):
            if name_contains:
                needle = name_contains.lower()
                entries = [e for e in entries if needle in str(e.get("name", "")).lower()]
            entries.sort(key=lambda e: (e.get("type") != "dir", str(e.get("name", ""))))
            total = len(entries)
            truncated = False
            if max_entries and total > max_entries:
                entries = entries[:max_entries]
                truncated = True
            payload = {
                "path": path,
                "total_entries": total,
                "returned_entries": len(entries),
                "truncated": truncated,
                "entries": entries,
            }
            return _fmt(payload)
        return _fmt(result)
    except Exception as exc:
        return f"Error listing directory: {exc}"


@mcp.tool()
async def read_file(
    system_name: str,
    path: str,
    mode: str = "head",
    lines: int = 50,
    max_chars: int = 20000,
    full: bool = False,
) -> str:
    """Read bounded content from a file on a NERSC filesystem.

    Args:
        system_name: System to read from, e.g. "perlmutter".
        path: Absolute path to the file.
        mode: 'head', 'tail', or 'view'. Defaults to 'head'.
        lines: Number of lines for head/tail mode. Clamped to 1..200.
        max_chars: Maximum characters to return (default 20000). Use 0 for no character cap.
        full: If true, return full content and skip head/tail selection. Use sparingly.

    Requires authentication.
    """
    lines = clamp_int(lines, minimum=1, maximum=200)
    try:
        content = await client.fs_download(system_name, path)
        return bounded_file_text(
            content, path=path, mode=mode, lines=lines, max_chars=max_chars, full=full
        )
    except Exception as exc:
        return f"Error reading file: {exc}"


@mcp.tool()
async def download_file(
    system_name: str,
    path: str,
    max_chars: int = 20000,
    full: bool = False,
) -> str:
    """Download bounded content from the NERSC filesystem.

    Tries the IRI view endpoint first (fast, in-process). If that fails,
    falls back to a Globus Transfer from the appropriate NERSC endpoint
    to a local temp file.

    Args:
        system_name: System to read from, e.g. "perlmutter".
        path: Absolute path to the file.
            /pscratch/...        → Perlmutter scratch Globus endpoint
            /global/homes/...    → NERSC DTN Globus endpoint
            /global/cfs/...      → NERSC DTN Globus endpoint
        max_chars: Maximum characters to return (default 20000). Use 0 for no character cap.
        full: If true, return full content. Use sparingly for large files.

    Returns bounded content by default to protect agent context.
    Requires authentication + authenticate_globus_transfer().
    """
    try:
        content = await client.fs_download(system_name, path)
        return bounded_file_text(
            content, path=path, mode="view", lines=0, max_chars=max_chars, full=full
        )
    except Exception as iri_exc:
        iri_error = str(iri_exc)
        logger.info("IRI download failed (%s), falling back to Globus transfer", iri_error)

    try:
        content = await client.globus_transfer_download(path)
        return bounded_file_text(
            content, path=path, mode="view", lines=0, max_chars=max_chars, full=full
        )
    except Exception as globus_exc:
        return f"Error downloading file: IRI failed ({iri_error}), Globus transfer also failed ({globus_exc})"


@mcp.tool()
async def upload_file(system_name: str, path: str, content: str) -> str:
    """Upload a small text file to a NERSC filesystem.

    Args:
        system_name: System to upload to, e.g. "perlmutter".
        path: Absolute destination path on the remote filesystem.
        content: The text content to upload.

    Max file size is 5 MB. Requires authentication.
    """
    try:
        result = await client.fs_upload(system_name, path, content)
        return _fmt(result)
    except Exception as exc:
        return f"Error uploading file: {exc}"


# ── Globus Data Movement ───────────────────────────────────────────────


@mcp.tool()
async def authenticate_globus_transfer(token: str = "") -> str:
    """Store a Globus Transfer API token for data movement to/from NERSC.

    Args:
        token: A Globus OAuth2 access token with the transfer scope
               (urn:globus:auth:scope:transfer.api.globus.org:all).
               If empty, falls back to GLOBUS_TRANSFER_TOKEN in .env
               (re-read at call time). So a bare
               authenticate_globus_transfer() succeeds whenever .env
               already has a fresh token.

    This is separate from the NERSC token. Required before using
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
        ep_id = await client._resolve_endpoint("nersc")
        return (
            f"Globus Transfer token stored and saved to {_ENV_FILE}. "
            f"Verified: nersc#dtn -> {ep_id}"
        )
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
        endpoint: Globus endpoint — use a shorthand or UUID.
                  Shorthands: "nersc" / "dtn" (NERSC DTN, CFS/homes),
                              "perlmutter" (Perlmutter scratch),
                              "hpss" (NERSC tape archive).
        path: Absolute path to list.
        show_hidden: Whether to include hidden files.

    Common NERSC paths via Globus:
      - Home:    /global/homes/<initial>/<username>
      - Scratch: /pscratch/sd/<initial>/<username>
      - CFS:     /global/cfs/cdirs/<project>

    Requires authenticate_globus_transfer() first.
    """
    try:
        result = await client.globus_ls(endpoint, path, show_hidden)
        entries = result.get("DATA", [])
        simplified = [
            {
                "name": e.get("name"),
                "type": e.get("type"),
                "size": e.get("size"),
                "last_modified": e.get("last_modified"),
            }
            for e in entries
        ]
        return _fmt({"path": path, "endpoint": endpoint, "entries": simplified})
    except Exception as exc:
        return f"Error listing directory: {exc}"


@mcp.tool()
async def globus_mkdir(endpoint: str, path: str, parents: bool = True) -> str:
    """Create a directory on a Globus endpoint before staging into it.

    Idempotent: a directory that already exists is reported, not an error. With
    `parents=True` (default) every missing ancestor is created top-down, so a
    full path like `/pscratch/sd/<i>/<your-username>/run1` is made in one call.

    Call this on the destination directory before `globus_transfer` so staging
    never fails on a missing folder. `endpoint` takes the same shorthand
    ("nersc", "perlmutter"), display name, or UUID as the other Globus tools.
    Requires authenticate_globus_transfer() first.
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
        source_endpoint: Source endpoint — shorthand or UUID.
                         NERSC shorthands: "nersc", "perlmutter", "hpss".
                         For local transfers use your Globus Connect Personal UUID.
        source_path: Absolute path on the source endpoint.
        dest_endpoint: Destination endpoint (same format as source).
        dest_path: Absolute path on the destination endpoint.
        label: Human-readable label for the transfer task.
        recursive: Set to true to transfer a directory recursively.

    Returns a task_id to track progress with globus_transfer_status().
    Requires authenticate_globus_transfer() first.
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
            "files": task.get("files", 0),
            "files_transferred": task.get("files_transferred", 0),
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
