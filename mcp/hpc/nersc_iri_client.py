# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""Async HTTP client for the NERSC IRI API and Globus Transfer.

Authentication: NERSC IRI uses a Globus OAuth2 bearer token. Obtain one with
`python scripts/auth/nersc_iri_token.py` and store it in `IRI_TOKEN_NERSC`
in .env.

API docs: https://api.iri.nersc.gov
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # mcp/ root
from auth_env import environ_token as _environ_token
from auth_env import local_globus_endpoint as _local_globus_endpoint

DEFAULT_BASE_URL = "https://api.iri.nersc.gov/api/v1"
GLOBUS_TRANSFER_BASE_URL = "https://transfer.api.globus.org/v0.10"
TASK_POLL_INTERVAL = 2.0
TASK_POLL_MAX_ATTEMPTS = 60

# Well-known NERSC Globus endpoint UUIDs
NERSC_GLOBUS_ENDPOINTS = {
    "nersc": "9d6d994a-6d04-11e5-ba46-22000b92c6ec",       # NERSC DTN (CFS / global homes)
    "dtn": "9d6d994a-6d04-11e5-ba46-22000b92c6ec",
    "hpss": "9cd89cfd-6d04-11e5-ba46-22000b92c6ec",        # NERSC HPSS
    "perlmutter": "6bdc7956-fc0f-4ad2-989c-7aa5ee643a79",  # Perlmutter scratch
    "nersc#dtn": "9d6d994a-6d04-11e5-ba46-22000b92c6ec",
}

# NERSC IRI resource UUIDs (from /api/v1/status/resources).
# Both "perlmutter" (familiar system name) and "compute" (canonical IRI
# resource name returned by /status/resources) point at the same UUID so
# job tools accept whichever the caller used for filesystem tools.
NERSC_COMPUTE_RESOURCES = {
    "perlmutter": "94351904-6dba-4c16-b5cd-fbd280d8615b",
    "compute": "94351904-6dba-4c16-b5cd-fbd280d8615b",
    "login": "e525a224-61c1-419f-9642-91168c792e39",
    "cori": "83c6f7bc-4c40-420a-bf43-e766b9ab0557",
}

NERSC_STORAGE_RESOURCES = {
    "homes": "65b28619-c3b6-4942-8da1-044a3b3a2a9e",
    "scratch": "43d8f6c0-f900-48ce-b267-73714103f4ac",
    "cfs": "59e80c79-4dfd-4c53-9c07-7405685fcd37",
    "common": "7e07a611-f927-4a39-a44d-b1d6e307accd",
    "archive": "f4916c65-9001-49c2-b0bf-6fe4276b564c",
}


def _fs_resource_for_path(path: str) -> str:
    """Pick the storage resource UUID that hosts the given absolute path."""
    if path.startswith("/pscratch/"):
        return NERSC_STORAGE_RESOURCES["scratch"]
    if path.startswith("/global/cfs/"):
        return NERSC_STORAGE_RESOURCES["cfs"]
    if path.startswith("/global/common/"):
        return NERSC_STORAGE_RESOURCES["common"]
    # /global/homes/<x>/<user> or /global/u<N>/... → homes
    return NERSC_STORAGE_RESOURCES["homes"]


class NERSCIRIClient:
    """Thin async wrapper around the NERSC IRI REST API."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("NERSC_IRI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.token: str | None = _environ_token("nersc") or None
        self.transfer_token: str | None = os.environ.get("GLOBUS_TRANSFER_TOKEN")
        # Callables returning the CURRENT token from its source of truth (the
        # per-user .env file), set by the server so every request re-reads the
        # freshest token rather than a value cached at startup.
        self.token_provider = None
        self.transfer_token_provider = None
        self._client = httpx.AsyncClient(timeout=60.0)

    # -- auth -----------------------------------------------------------

    def set_token(self, token: str) -> None:
        self.token = token

    def set_transfer_token(self, token: str) -> None:
        self.transfer_token = token

    def _current_token(self) -> str | None:
        """Freshest NERSC token: provider (file) wins, else the cached value."""
        if self.token_provider:
            try:
                fresh = self.token_provider()
            except Exception:
                fresh = ""
            if fresh:
                self.token = fresh
        return self.token

    def _headers(self) -> dict[str, str]:
        token = self._current_token()
        if not token:
            raise RuntimeError("No NERSC token set. Call authenticate() first.")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _transfer_headers(self) -> dict[str, str]:
        token = ""
        if self.transfer_token_provider:
            try:
                token = self.transfer_token_provider() or ""
            except Exception:
                token = ""
            if token:
                self.transfer_token = token
        token = token or self.transfer_token or os.environ.get("GLOBUS_TRANSFER_TOKEN")
        if not token:
            raise RuntimeError("No Globus Transfer token. Call authenticate_globus_transfer() first.")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # -- low-level helpers ----------------------------------------------

    @staticmethod
    def _raise_with_body(resp) -> None:
        """Raise RuntimeError with response body included for diagnostics."""
        if resp.is_error:
            body = resp.text[:2000] if resp.text else "(empty body)"
            raise RuntimeError(f"HTTP {resp.status_code} for {resp.url} | body: {body}")

    async def _get(self, path: str, *, params: dict | None = None, auth: bool = True) -> dict | list:
        headers = self._headers() if auth else {"Accept": "application/json"}
        resp = await self._client.get(
            f"{self.base_url}/{path.lstrip('/')}",
            headers=headers,
            params=params,
        )
        self._raise_with_body(resp)
        return resp.json()

    async def _post(self, path: str, *, data: dict | None = None, files=None) -> dict | list:
        if files:
            resp = await self._client.post(
                f"{self.base_url}/{path.lstrip('/')}",
                headers={"Authorization": f"Bearer {self._current_token()}", "Accept": "application/json"},
                files=files,
                data=data,
            )
        else:
            resp = await self._client.post(
                f"{self.base_url}/{path.lstrip('/')}",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=data or {},
            )
        self._raise_with_body(resp)
        return resp.json()

    async def _delete(self, path: str, *, params: dict | None = None) -> dict:
        resp = await self._client.delete(
            f"{self.base_url}/{path.lstrip('/')}",
            headers=self._headers(),
            params=params,
        )
        self._raise_with_body(resp)
        if not resp.content:
            return {"status": "ok", "http_status": resp.status_code}
        return resp.json()

    async def _poll_task(self, task_id: str) -> dict:
        """Poll GET /task/{task_id} until status is completed or failed."""
        for _ in range(TASK_POLL_MAX_ATTEMPTS):
            data = await self._get(f"task/{task_id}")
            status = data.get("status", "")
            if status in ("completed", "failed", "cancelled"):
                return data
            await asyncio.sleep(TASK_POLL_INTERVAL)
        raise TimeoutError(f"Task {task_id} did not complete within {TASK_POLL_MAX_ATTEMPTS * TASK_POLL_INTERVAL}s")

    async def _async_get(self, path: str, *, params: dict | None = None) -> dict:
        """Make a request that returns a task_id, then poll until done."""
        data = await self._get(path, params=params)
        task_id = data.get("task_id")
        if not task_id:
            return data
        result = await self._poll_task(task_id)
        if result.get("status") == "failed":
            raise RuntimeError(f"Task failed: {result.get('error', result)}")
        return result.get("result", result)

    # -- status (no auth) -----------------------------------------------

    async def list_systems(self) -> list:
        """GET /status/resources — list all NERSC resources and their status."""
        return await self._get("status/resources", auth=False)

    async def get_system_status(self, system_name: str) -> dict:
        """Find a resource by name or UUID from /status/resources.

        Accepts system names like "perlmutter" (mapped to resource UUID first),
        canonical IRI names like "compute", or a raw resource UUID.
        """
        resources = await self._get("status/resources", auth=False)
        name_lower = system_name.lower()
        # Try direct name match first
        for r in resources:
            if r.get("name", "").lower() == name_lower:
                return r
        # Try resolving via compute resource UUID (e.g. "perlmutter" → compute UUID)
        rid = NERSC_COMPUTE_RESOURCES.get(name_lower)
        if rid:
            for r in resources:
                if r.get("id") == rid:
                    return r
        # Try UUID match
        for r in resources:
            if r.get("id") == system_name:
                return r
        raise RuntimeError(f"System '{system_name}' not found in NERSC resources")

    # -- account --------------------------------------------------------

    async def get_account(self) -> dict:
        """GET /account/projects — return first project as account proxy."""
        projects = await self._get("account/projects")
        if isinstance(projects, list) and projects:
            return projects[0]
        return {}

    async def list_projects(self) -> list:
        """GET /account/projects — list user's NERSC projects."""
        return await self._get("account/projects")

    async def get_project(self, project_id: str) -> dict:
        """GET /account/projects/{project_id}."""
        return await self._get(f"account/projects/{project_id}")

    # -- compute --------------------------------------------------------

    def _compute_resource_id(self, system_name: str) -> str:
        rid = NERSC_COMPUTE_RESOURCES.get(system_name.lower())
        if not rid:
            raise RuntimeError(
                f"Unknown system '{system_name}'. Known: {list(NERSC_COMPUTE_RESOURCES)}"
            )
        return rid

    async def list_jobs(self, system_name: str, user: str | None = None) -> list:
        """POST /compute/status/{resource_id} — list jobs, filtered to `user`.

        If user is None, auto-discovers the authenticated user from account/projects.
        """
        rid = self._compute_resource_id(system_name)
        if user is None:
            try:
                projects = await self._get("account/projects")
                if isinstance(projects, list) and projects:
                    uids = projects[0].get("user_ids") or []
                    user = uids[0] if uids else None
            except Exception:
                pass
        body = {"user": user} if user else {}
        result = await self._post(f"compute/status/{rid}", data=body)
        if isinstance(result, list):
            return result
        return result.get("output", result.get("jobs", []))

    async def get_job_status(self, system_name: str, job_id: str) -> dict:
        """GET /compute/status/{resource_id}/{job_id} — job details."""
        rid = self._compute_resource_id(system_name)
        return await self._get(f"compute/status/{rid}/{job_id}")

    @staticmethod
    def _sbatch_to_jobspec(script: str) -> dict:
        """Parse a #SBATCH script into a structured JobSpec dict.

        SBATCH directives become resources/attributes; the remaining
        command lines become pre_launch; executable is set to /bin/bash -c exit 0.
        """
        import re
        duration = None
        node_count = None
        gpu_count = None
        account = None
        queue_name = None
        job_name = None
        stdout_path = None
        stderr_path = None
        custom_attributes: dict = {}
        commands: list[str] = []

        for line in script.splitlines():
            stripped = line.strip()
            if stripped.startswith("#!/"):
                continue
            if stripped.startswith("#SBATCH"):
                m = re.match(r"#SBATCH\s+--?([\w-]+)(?:[= ](.*))?", stripped)
                if not m:
                    continue
                key = m.group(1).replace("-", "_")
                val = (m.group(2) or "").strip().strip('"').strip("'")
                if key in ("array", "a"):
                    # IRI compute/job submits one job at a time — Slurm arrays
                    # cannot be expressed in the JobSpec, so a silent single-task
                    # run is the wrong default. Surface it loudly.
                    raise ValueError(
                        f"NERSC IRI submit_job cannot express Slurm arrays "
                        f"('#SBATCH --array={val}'). Submit one job per array "
                        f"index instead, or call sbatch directly via the "
                        f"perlmutter-services ClearML queue."
                    )
                if key in ("time", "t"):
                    parts = val.split(":")
                    if len(parts) == 3:
                        duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    elif len(parts) == 2:
                        duration = int(parts[0]) * 60 + int(parts[1])
                    else:
                        duration = int(val)
                elif key in ("nodes", "N"):
                    node_count = int(val)
                elif key in ("account", "A"):
                    account = val
                elif key in ("qos", "partition", "p", "q"):
                    queue_name = val
                elif key in ("job_name", "J"):
                    job_name = val
                elif key in ("output", "o"):
                    stdout_path = val
                elif key in ("error", "e"):
                    stderr_path = val
                elif key in ("constraint", "C"):
                    custom_attributes["constraint"] = val
                elif key in ("gpus_per_node", "gpus_per_task", "gpus", "G"):
                    try:
                        gpu_count = int(val)
                    except (ValueError, TypeError):
                        gpu_count = 1
                # skip other SBATCH options
            elif stripped.startswith("#"):
                continue
            else:
                commands.append(line)

        pre_launch = "\n".join(commands).strip()
        body: dict = {
            "executable": "/bin/bash",
            "arguments": ["-c", "exit 0"],
        }
        if job_name:
            body["name"] = job_name
        if stdout_path:
            body["stdout_path"] = stdout_path
        if stderr_path:
            body["stderr_path"] = stderr_path
        if pre_launch:
            body["pre_launch"] = pre_launch

        resources: dict = {}
        if node_count:
            resources["node_count"] = node_count
        if gpu_count:
            resources["gpu_cores_per_process"] = gpu_count
        if resources:
            body["resources"] = resources

        attributes: dict = {}
        if duration:
            attributes["duration"] = duration
        if queue_name:
            attributes["queue_name"] = queue_name
        if account:
            attributes["account"] = account
        if custom_attributes:
            attributes["custom_attributes"] = custom_attributes
        if attributes:
            body["attributes"] = attributes

        return body

    async def submit_job(self, system_name: str, script: str, is_path: bool = False) -> dict:
        """POST /compute/job/{resource_id} — submit a Slurm job.

        Accepts either an inline #SBATCH script (is_path=False, default) or
        an absolute path to a script already on a NERSC filesystem
        (is_path=True). In both cases the SBATCH directives are parsed into
        the structured JobSpec — running an SBATCH file as bare `bash <path>`
        would silently drop --account / --qos / --constraint / --time and
        land the job on the user's default partition (e.g. CPU regular_milan
        instead of GPU), so that path is not used.
        """
        rid = self._compute_resource_id(system_name)
        if is_path:
            # Read the script content from the remote filesystem so we can
            # parse #SBATCH directives the same way as the inline path.
            content = await self.fs_download(system_name, script)
            if not content or not content.strip():
                raise RuntimeError(
                    f"NERSC IRI submit_job: could not read script content from {script!r} "
                    f"(empty response from filesystem/view). Check the path and permissions."
                )
            body = self._sbatch_to_jobspec(content)
        else:
            body = self._sbatch_to_jobspec(script)
        return await self._post(f"compute/job/{rid}", data=body)

    async def cancel_job(self, system_name: str, job_id: str) -> dict:
        """DELETE /compute/cancel/{resource_id}/{job_id} — cancel a job."""
        rid = self._compute_resource_id(system_name)
        return await self._delete(f"compute/cancel/{rid}/{job_id}")

    # -- filesystem (async task pattern) --------------------------------

    async def fs_ls(self, system_name: str, path: str) -> dict:
        """GET /filesystem/ls/{resource_id}?path= — list a directory (async task).

        Retries once on the transient `find: '<path>': No such file or
        directory` race the IRI filesystem-router hits when an entry
        (typically a per-PID bash `.history.<pid>` file) disappears between
        readdir and stat. The race re-fires whenever a login session ends
        during a `find` walk and is not fixable on the user side.
        """
        rid = _fs_resource_for_path(path)
        try:
            result = await self._async_get(f"filesystem/ls/{rid}", params={"path": path})
        except RuntimeError as exc:
            msg = str(exc)
            if "find:" in msg and "No such file" in msg:
                await asyncio.sleep(0.5)
                result = await self._async_get(f"filesystem/ls/{rid}", params={"path": path})
            else:
                raise
        # Normalize: result may be {"output": [...]} or a list directly
        if isinstance(result, dict) and "output" in result:
            return result
        return {"output": result if isinstance(result, list) else [result]}

    async def fs_download(self, system_name: str, path: str) -> str:
        """GET /filesystem/view/{resource_id}?path= — view file content (async task).

        Response structure (after task polling):
          result["result"]["output"]["content"] = file content string
        """
        rid = _fs_resource_for_path(path)
        result = await self._async_get(f"filesystem/view/{rid}", params={"path": path})
        # result = {"output": {"content": "...", "content_type": "bytes", ...}}
        if isinstance(result, dict):
            output = result.get("output", result)
            if isinstance(output, dict):
                return str(output.get("content", str(output)))
            if isinstance(output, list):
                return "\n".join(str(item) for item in output)
            return str(output)
        return str(result)

    async def fs_upload(self, system_name: str, path: str, content: str) -> dict:
        """Upload via POST /filesystem/upload/{resource_id}?path=<path> (multipart, async)."""
        import io
        rid = _fs_resource_for_path(path)
        filename = path.split("/")[-1]
        files = {
            "file": (filename, io.BytesIO(content.encode()), "application/octet-stream"),
        }
        resp = await self._client.post(
            f"{self.base_url}/filesystem/upload/{rid}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            params={"path": path},
            files=files,
        )
        self._raise_with_body(resp)
        data = resp.json()
        task_id = data.get("task_id")
        if task_id:
            result = await self._poll_task(task_id)
            if result.get("status") == "failed":
                err_blob = str(result.get("error", result))
                hint = ""
                if "No such file" in err_blob or "does not exist" in err_blob:
                    parent = "/".join(path.rstrip("/").split("/")[:-1]) or "/"
                    hint = (
                        f" (hint: parent directory '{parent}' may not exist on the "
                        f"target filesystem — IRI upload does not auto-create parents)"
                    )
                raise RuntimeError(f"Upload task failed: {result.get('error', result)}{hint}")
            return {"status": "ok", "path": path, "task_id": task_id}
        return data

    def _local_endpoint(self) -> str:
        """UUID of this machine's Globus Connect Personal collection.

        Delegates to the one canonical autodetect in auth_env so all three
        facility clients resolve the local endpoint the same way.
        """
        return _local_globus_endpoint()

    async def globus_transfer_download(self, path: str) -> str:
        """Download a file from NERSC via Globus Transfer to a local temp file.

        Automatically selects the correct NERSC endpoint:
          /pscratch/...  → Perlmutter scratch endpoint
          everything else → NERSC DTN (homes, CFS)
        """
        import tempfile
        from pathlib import Path

        if path.startswith("/pscratch/"):
            remote_ep = NERSC_GLOBUS_ENDPOINTS["perlmutter"]
        else:
            remote_ep = NERSC_GLOBUS_ENDPOINTS["nersc"]

        local_ep = self._local_endpoint()

        tmp_dir = Path.home() / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=tmp_dir, prefix="nersc_download_", suffix=Path(path).suffix,
            delete=False,
        ) as f:
            local_path = f.name

        try:
            result = await self.globus_transfer(
                source_endpoint=remote_ep,
                source_path=path,
                dest_endpoint=local_ep,
                dest_path=local_path,
                label=f"download {Path(path).name}",
            )
            task_id = result.get("task_id", "")
            # Poll until done
            for _ in range(60):
                task = await self.globus_get_task(task_id)
                if task.get("status") in ("SUCCEEDED", "FAILED", "INACTIVE"):
                    break
                await asyncio.sleep(3.0)
            if task.get("status") != "SUCCEEDED":
                raise RuntimeError(f"Globus transfer failed: {task.get('nice_status_details', task.get('status'))}")
            return Path(local_path).read_text()
        finally:
            try:
                os.unlink(local_path)
            except OSError:
                pass

    # -- Globus Transfer -----------------------------------------------

    async def _resolve_endpoint(self, name_or_id: str) -> str:
        """Resolve a friendly name or UUID to a Globus endpoint UUID."""
        key = name_or_id.lower()
        if key in NERSC_GLOBUS_ENDPOINTS:
            return NERSC_GLOBUS_ENDPOINTS[key]
        if len(name_or_id) == 36 and name_or_id.count("-") == 4:
            return name_or_id
        resp = await self._client.get(
            f"{GLOBUS_TRANSFER_BASE_URL}/endpoint_search",
            headers=self._transfer_headers(),
            params={"filter_fulltext": name_or_id, "limit": 5},
        )
        resp.raise_for_status()
        data = resp.json().get("DATA", [])
        for ep in data:
            if ep.get("canonical_name") == name_or_id or ep.get("display_name") == name_or_id:
                return ep["id"]
        if data:
            return data[0]["id"]
        raise RuntimeError(f"Could not resolve Globus endpoint: {name_or_id}")

    async def globus_ls(self, endpoint: str, path: str, show_hidden: bool = False) -> dict:
        ep_id = await self._resolve_endpoint(endpoint)
        params: dict = {"path": path}
        if show_hidden:
            params["show_hidden"] = "1"
        resp = await self._client.get(
            f"{GLOBUS_TRANSFER_BASE_URL}/operation/endpoint/{ep_id}/ls",
            headers=self._transfer_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def globus_transfer(
        self,
        source_endpoint: str,
        source_path: str,
        dest_endpoint: str,
        dest_path: str,
        label: str = "MCP transfer",
        recursive: bool = False,
    ) -> dict:
        src_id = await self._resolve_endpoint(source_endpoint)
        dst_id = await self._resolve_endpoint(dest_endpoint)

        submission_id_resp = await self._client.get(
            f"{GLOBUS_TRANSFER_BASE_URL}/submission_id",
            headers=self._transfer_headers(),
        )
        submission_id_resp.raise_for_status()
        submission_id = submission_id_resp.json()["value"]

        transfer_doc = {
            "DATA_TYPE": "transfer",
            "submission_id": submission_id,
            "source_endpoint": src_id,
            "destination_endpoint": dst_id,
            "label": label,
            "DATA": [
                {
                    "DATA_TYPE": "transfer_item",
                    "source_path": source_path,
                    "destination_path": dest_path,
                    "recursive": recursive,
                }
            ],
        }
        resp = await self._client.post(
            f"{GLOBUS_TRANSFER_BASE_URL}/transfer",
            headers=self._transfer_headers(),
            json=transfer_doc,
        )
        resp.raise_for_status()
        return resp.json()

    async def _mkdir_one(self, ep_id: str, path: str) -> bool:
        """Create a single directory on an endpoint. True if created, False if
        it already existed. Raises on any other error (e.g. permission).

        Globus mkdir returns HTTP 502 with an `*Exists` code / "already exists"
        message when the directory is already there; that is treated as success.
        """
        resp = await self._client.post(
            f"{GLOBUS_TRANSFER_BASE_URL}/operation/endpoint/{ep_id}/mkdir",
            headers=self._transfer_headers(),
            json={"DATA_TYPE": "mkdir", "path": path},
        )
        if resp.status_code < 400:
            return True
        try:
            body = resp.json()
        except Exception:
            body = {}
        blob = f"{body.get('code', '')} {body.get('message', '')}".lower()
        if "exists" in blob:
            return False
        resp.raise_for_status()
        return False  # unreachable

    async def globus_mkdir(self, endpoint: str, path: str, parents: bool = True) -> dict:
        """Create a directory on a Globus endpoint, idempotently.

        Native Globus mkdir makes a single level and errors if the directory
        already exists or a parent is missing. This wrapper treats "already
        exists" as success and, with parents=True (the default), creates each
        missing ancestor top-down so a full path like
        /pscratch/sd/<i>/<user>/run1 comes into being in one call. Use it to
        ensure a staging destination exists before globus_transfer.
        """
        ep_id = await self._resolve_endpoint(endpoint)
        clean = "/" + path.strip("/")
        parts = [p for p in clean.split("/") if p]
        if parents:
            targets, accum = [], ""
            for p in parts:
                accum += "/" + p
                targets.append(accum)
        else:
            targets = [clean]
        created, existed = [], []
        for target in targets:
            (created if await self._mkdir_one(ep_id, target) else existed).append(target)
        return {"endpoint": endpoint, "path": clean, "created": created, "already_existed": existed}

    async def globus_get_task(self, task_id: str) -> dict:
        resp = await self._client.get(
            f"{GLOBUS_TRANSFER_BASE_URL}/task/{task_id}",
            headers=self._transfer_headers(),
        )
        resp.raise_for_status()
        return resp.json()
