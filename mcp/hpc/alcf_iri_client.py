# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""Async HTTP client for the ALCF IRI Facility API and Globus Transfer."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # mcp/ root
from auth_env import environ_token as _environ_token
from auth_env import local_globus_endpoint as _local_globus_endpoint

DEFAULT_BASE_URL = "https://api.alcf.anl.gov/api/v1"
GLOBUS_TRANSFER_BASE_URL = "https://transfer.api.globus.org/v0.10"
TASK_POLL_INTERVAL = 2.0
TASK_POLL_MAX_ATTEMPTS = 30

# Well-known ALCF Globus endpoint display names and verified UUIDs
ALCF_GLOBUS_ENDPOINTS = {
    "home": "alcf#dtn_home",
    "eagle": "alcf#dtn_eagle",
    "grand": "alcf#dtn_grand",
    "flare": "alcf#dtn_flare",
}

ALCF_GLOBUS_ENDPOINT_IDS = {
    "home": "9032dd3a-e841-4687-a163-2720da731b5b",
    "eagle": "05d2c76a-e867-4f67-aa57-76edeb0beda0",
    "alcf#dtn_home": "9032dd3a-e841-4687-a163-2720da731b5b",
    "alcf#dtn_eagle": "05d2c76a-e867-4f67-aa57-76edeb0beda0",
}

# Map IRI storage resource UUIDs to Globus endpoint IDs
IRI_RESOURCE_TO_GLOBUS = {
    "6115bd2c-957a-4543-abff-5fae52992ff2": "9032dd3a-e841-4687-a163-2720da731b5b",  # Home
    "1c3ad9d4-2e91-42bc-becb-72b1fde1235c": "05d2c76a-e867-4f67-aa57-76edeb0beda0",  # Eagle
}

GLOBUS_HTTPS_BASE = "https://g-{ep_id}.data.globus.org"


class ALCFIRIClient:
    """Thin async wrapper around the ALCF IRI REST API."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("ALCF_IRI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.token: str | None = _environ_token("alcf") or None
        self.transfer_token: str | None = os.environ.get("GLOBUS_TRANSFER_TOKEN")
        # Optional callables that return the CURRENT token from its source of
        # truth (the per-user .env file). Set by the server so every request
        # re-reads the freshest token instead of a value cached at startup —
        # tokens are rotated externally (login/refresh) mid-session.
        self.token_provider = None
        self.transfer_token_provider = None
        self._client = httpx.AsyncClient(timeout=60.0)

    # -- auth -----------------------------------------------------------

    def set_token(self, token: str) -> None:
        self.token = token

    def _current_token(self) -> str | None:
        """Freshest IRI token: provider (file) wins, else the cached value."""
        if self.token_provider:
            try:
                fresh = self.token_provider()
            except Exception:
                fresh = ""
            if fresh:
                self.token = fresh
        return self.token

    def _headers(self, auth_required: bool = True) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = self._current_token()
        if token and auth_required:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # -- low-level request helpers -------------------------------------

    @staticmethod
    def _raise_with_body(resp) -> None:
        """Raise RuntimeError with response body included for diagnostics."""
        if resp.is_error:
            body = resp.text[:2000] if resp.text else "(empty body)"
            raise RuntimeError(f"HTTP {resp.status_code} for {resp.url} | body: {body}")

    async def _get(self, path: str, *, params: dict | None = None, auth: bool = True) -> dict | list:
        resp = await self._client.get(
            f"{self.base_url}/{path.lstrip('/')}",
            headers=self._headers(auth),
            params=params,
        )
        self._raise_with_body(resp)
        return resp.json()

    async def _post(self, path: str, *, data: dict | None = None, auth: bool = True) -> dict | list:
        resp = await self._client.post(
            f"{self.base_url}/{path.lstrip('/')}",
            headers={**self._headers(auth), "Content-Type": "application/json"},
            json=data,
        )
        self._raise_with_body(resp)
        return resp.json()

    async def _put(self, path: str, *, data: dict | None = None, auth: bool = True) -> dict | list:
        resp = await self._client.put(
            f"{self.base_url}/{path.lstrip('/')}",
            headers={**self._headers(auth), "Content-Type": "application/json"},
            json=data,
        )
        self._raise_with_body(resp)
        return resp.json()

    async def _delete(self, path: str, *, auth: bool = True) -> int:
        resp = await self._client.delete(
            f"{self.base_url}/{path.lstrip('/')}",
            headers=self._headers(auth),
        )
        self._raise_with_body(resp)
        return resp.status_code

    # -- facility (no auth) -------------------------------------------

    async def get_facility(self) -> dict:
        return await self._get("facility", auth=False)

    async def list_sites(self) -> list:
        return await self._get("facility/sites", auth=False)

    # -- status (no auth) ---------------------------------------------

    async def list_resources(self) -> list:
        return await self._get("status/resources", auth=False)

    async def get_resource(self, resource_id: str) -> dict:
        return await self._get(f"status/resources/{resource_id}", auth=False)

    async def list_incidents(self, resource_id: str | None = None) -> list:
        params = {}
        if resource_id:
            params["resource_id"] = resource_id
        return await self._get("status/incidents", params=params or None, auth=False)

    # -- account -------------------------------------------------------

    async def list_projects(self) -> list:
        return await self._get("account/projects")

    async def get_project(self, project_id: str) -> dict:
        return await self._get(f"account/projects/{project_id}")

    async def get_project_allocations(self, project_id: str) -> list:
        return await self._get(f"account/projects/{project_id}/project_allocations")

    # -- compute -------------------------------------------------------

    async def submit_job(self, resource_id: str, job_spec: dict) -> dict:
        return await self._post(f"compute/job/{resource_id}", data=job_spec)

    async def get_job_status(self, resource_id: str, job_id: str) -> dict:
        return await self._get(f"compute/status/{resource_id}/{job_id}")

    async def list_jobs(self, resource_id: str) -> list:
        return await self._post(f"compute/status/{resource_id}", data={})

    async def cancel_job(self, resource_id: str, job_id: str) -> int:
        return await self._delete(f"compute/cancel/{resource_id}/{job_id}")

    # -- filesystem (async task-based) ---------------------------------

    async def fs_ls(self, resource_id: str, path: str, show_hidden: bool = False) -> dict:
        # ALCF IRI requires paths prefixed with /eagle/, /home/, or /lus/eagle/
        # Param name is showHidden (camelCase), not show_hidden
        params = {"path": path, "showHidden": show_hidden}
        return await self._get(f"filesystem/ls/{resource_id}", params=params)

    async def fs_head(self, resource_id: str, path: str, lines: int = 20) -> dict:
        params = {"path": path, "lines": lines}
        return await self._get(f"filesystem/head/{resource_id}", params=params)

    async def fs_view(self, resource_id: str, path: str) -> dict:
        params = {"path": path}
        return await self._get(f"filesystem/view/{resource_id}", params=params)

    async def fs_tail(self, resource_id: str, path: str, lines: int = 20) -> dict:
        params = {"path": path, "lines": lines}
        return await self._get(f"filesystem/tail/{resource_id}", params=params)

    async def fs_download(self, resource_id: str, path: str) -> dict:
        params = {"path": path}
        return await self._get(f"filesystem/download/{resource_id}", params=params)

    async def fs_upload(self, resource_id: str, path: str, content: str) -> dict:
        resp = await self._client.post(
            f"{self.base_url}/filesystem/upload/{resource_id}",
            headers=self._headers(True),
            params={"path": path},
            files={"file": ("upload", content.encode(), "application/octet-stream")},
        )
        resp.raise_for_status()
        return resp.json()

    # -- task ----------------------------------------------------------

    async def get_task(self, task_id: str) -> dict:
        return await self._get(f"task/{task_id}")

    async def list_tasks(self) -> list:
        return await self._get("task")

    async def wait_for_task(self, task_id: str) -> dict:
        """Poll a task until it completes or fails."""
        for _ in range(TASK_POLL_MAX_ATTEMPTS):
            task = await self.get_task(task_id)
            status = task.get("status", "")
            if status in ("completed", "failed", "canceled"):
                return task
            await asyncio.sleep(TASK_POLL_INTERVAL)
        return task  # return last state even if not done

    # -- Globus Transfer -----------------------------------------------

    def set_transfer_token(self, token: str) -> None:
        self.transfer_token = token

    def _transfer_headers(self) -> dict[str, str]:
        token = ""
        if getattr(self, "transfer_token_provider", None):
            try:
                token = self.transfer_token_provider() or ""
            except Exception:
                token = ""
            if token:
                self.transfer_token = token
        token = token or getattr(self, "transfer_token", None) or os.environ.get("GLOBUS_TRANSFER_TOKEN")
        if not token:
            raise RuntimeError("No Globus Transfer token. Call authenticate_globus_transfer() first.")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _resolve_endpoint(self, name_or_id: str) -> str:
        """Resolve a friendly name or display name to a Globus endpoint UUID.

        Accepts: "home", "eagle", "alcf#dtn_home", or a raw UUID.
        Uses cached UUIDs for known ALCF endpoints to avoid API calls.
        """
        key = name_or_id.lower()

        # Fast path: known ALCF endpoint UUIDs
        if key in ALCF_GLOBUS_ENDPOINT_IDS:
            return ALCF_GLOBUS_ENDPOINT_IDS[key]

        # Check shorthand aliases
        display_name = ALCF_GLOBUS_ENDPOINTS.get(key, name_or_id)
        if display_name in ALCF_GLOBUS_ENDPOINT_IDS:
            return ALCF_GLOBUS_ENDPOINT_IDS[display_name]

        # If it looks like a UUID already, return as-is
        if len(display_name) == 36 and display_name.count("-") == 4:
            return display_name

        # Search the Globus endpoint catalog
        resp = await self._client.get(
            f"{GLOBUS_TRANSFER_BASE_URL}/endpoint_search",
            headers=self._transfer_headers(),
            params={"filter_fulltext": display_name, "limit": 5},
        )
        resp.raise_for_status()
        data = resp.json().get("DATA", [])

        # Prefer exact match on canonical_name or display_name
        for ep in data:
            if ep.get("canonical_name") == display_name or ep.get("display_name") == display_name:
                return ep["id"]

        # Fall back to first result
        if data:
            return data[0]["id"]

        raise RuntimeError(f"Could not resolve Globus endpoint: {name_or_id}")

    async def globus_ls(self, endpoint: str, path: str, show_hidden: bool = False) -> dict:
        """List a directory on a Globus endpoint."""
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
        """Submit a Globus Transfer task."""
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
        /<project>/<user>/run1 comes into being in one call. Use it to ensure a
        staging destination exists before globus_transfer.
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
        """Get the status of a Globus Transfer task."""
        resp = await self._client.get(
            f"{GLOBUS_TRANSFER_BASE_URL}/task/{task_id}",
            headers=self._transfer_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def _local_endpoint(self) -> str:
        """UUID of this machine's Globus Connect Personal collection.

        Delegates to the one canonical autodetect in auth_env so all three
        facility clients resolve the local endpoint the same way.
        """
        return _local_globus_endpoint()

    def _iri_resource_to_globus_endpoint(self, resource_id: str) -> str:
        """Map an IRI storage resource UUID to its Globus endpoint UUID."""
        ep_id = IRI_RESOURCE_TO_GLOBUS.get(resource_id)
        if not ep_id:
            raise RuntimeError(
                f"No Globus endpoint mapping for IRI resource {resource_id}. "
                f"Known: {list(IRI_RESOURCE_TO_GLOBUS.keys())}"
            )
        return ep_id

    def _iri_path_to_globus_path(self, resource_id: str, path: str) -> str:
        """Convert an IRI filesystem path to a Globus-style path.

        Eagle IRI paths start with /eagle/; Globus Eagle paths do not.
        Home IRI paths start with /home/; Globus Home paths do not.
        """
        p = path
        # Eagle: strip leading /eagle
        if resource_id == "1c3ad9d4-2e91-42bc-becb-72b1fde1235c":
            if p.startswith("/eagle/"):
                p = p[len("/eagle"):]
            elif p.startswith("/eagle"):
                p = p[len("/eagle"):]
        # Home: strip leading /home  (Globus home uses /<username> directly)
        elif resource_id == "6115bd2c-957a-4543-abff-5fae52992ff2":
            if p.startswith("/home/"):
                p = p[len("/home"):]
        return p or "/"

    async def globus_wait_for_transfer(
        self, task_id: str, poll_interval: float = 3.0, max_wait: float = 300.0
    ) -> dict:
        """Poll a Globus Transfer task until it reaches a terminal state."""
        elapsed = 0.0
        while elapsed < max_wait:
            task = await self.globus_get_task(task_id)
            status = task.get("status", "ACTIVE")
            if status in ("SUCCEEDED", "FAILED", "INACTIVE"):
                return task
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        return task  # return last state even if still active

    async def globus_transfer_upload(
        self, resource_id: str, path: str, content: str
    ) -> dict:
        """Upload file content to an HPC filesystem via Globus task-based transfer.

        Writes content to a local temp file, transfers it to the remote endpoint,
        and waits for completion.
        """
        local_ep = self._local_endpoint()
        remote_ep = self._iri_resource_to_globus_endpoint(resource_id)
        remote_path = self._iri_path_to_globus_path(resource_id, path)

        # Write to a local temp file in ~/tmp/ (Globus Connect Personal can see home)
        tmp_dir = Path.home() / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=tmp_dir, prefix="alcf_upload_", suffix=Path(path).suffix,
            delete=False, mode="w"
        ) as f:
            f.write(content)
            local_path = f.name

        try:
            result = await self.globus_transfer(
                source_endpoint=local_ep,
                source_path=local_path,
                dest_endpoint=remote_ep,
                dest_path=remote_path,
                label=f"upload {Path(path).name}",
            )
            task_id = result.get("task_id", "")
            task = await self.globus_wait_for_transfer(task_id)
            if task.get("status") != "SUCCEEDED":
                raise RuntimeError(f"Globus transfer failed: {task.get('nice_status_details', task.get('status'))}")
            return {
                "status": "uploaded",
                "path": path,
                "method": "globus_transfer",
                "task_id": task_id,
                "size": len(content),
            }
        finally:
            try:
                os.unlink(local_path)
            except OSError:
                pass

    async def globus_transfer_download(
        self, resource_id: str, path: str
    ) -> str:
        """Download file content from an HPC filesystem via Globus task-based transfer.

        Transfers the file to a local temp path, reads it, and returns the content.
        """
        local_ep = self._local_endpoint()
        remote_ep = self._iri_resource_to_globus_endpoint(resource_id)
        remote_path = self._iri_path_to_globus_path(resource_id, path)

        tmp_dir = Path.home() / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=tmp_dir, prefix="alcf_download_", suffix=Path(path).suffix,
            delete=False
        ) as f:
            local_path = f.name

        try:
            result = await self.globus_transfer(
                source_endpoint=remote_ep,
                source_path=remote_path,
                dest_endpoint=local_ep,
                dest_path=local_path,
                label=f"download {Path(path).name}",
            )
            task_id = result.get("task_id", "")
            task = await self.globus_wait_for_transfer(task_id)
            if task.get("status") != "SUCCEEDED":
                raise RuntimeError(f"Globus transfer failed: {task.get('nice_status_details', task.get('status'))}")
            return Path(local_path).read_text()
        finally:
            try:
                os.unlink(local_path)
            except OSError:
                pass

    def _globus_https_url(self, resource_id: str, path: str) -> str:
        """Build a Globus HTTPS URL for a given IRI resource and path."""
        ep_id = IRI_RESOURCE_TO_GLOBUS.get(resource_id)
        if not ep_id:
            raise RuntimeError(
                f"No Globus endpoint mapping for IRI resource {resource_id}. "
                f"Known: {list(IRI_RESOURCE_TO_GLOBUS.keys())}"
            )
        base = GLOBUS_HTTPS_BASE.format(ep_id=ep_id)
        return f"{base}/{path.lstrip('/')}"

    async def globus_https_upload(self, resource_id: str, path: str, content: str) -> dict:
        """Upload file content via Globus HTTPS PUT."""
        url = self._globus_https_url(resource_id, path)
        headers = self._transfer_headers()
        headers["Content-Type"] = "application/octet-stream"
        resp = await self._client.put(url, headers=headers, content=content.encode())
        resp.raise_for_status()
        return {"status": "uploaded", "path": path, "method": "globus_https", "size": len(content)}

    async def globus_https_download(self, resource_id: str, path: str) -> str:
        """Download file content via Globus HTTPS GET."""
        url = self._globus_https_url(resource_id, path)
        headers = self._transfer_headers()
        headers.pop("Content-Type", None)
        resp = await self._client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text
