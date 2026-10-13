# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""Async HTTP client for the OLCF IRI Facility API and Globus Transfer.

Despite the s3m.olcf.ornl.gov hostname, this is the DOE IRI Facility API
v0.4.3 (paths /api/v1/facility, /api/v1/compute, /api/v1/status), NOT the
OPAT/Slurm S3M API used by Odo. The moderate enclave hosts Frontier here.

API surface (from /openapi.json on amsc-moderate):
  GET    /api/v1/facility
  GET    /api/v1/facility/sites
  GET    /api/v1/facility/sites/{site_id}
  GET    /api/v1/status/resources
  GET    /api/v1/status/resources/{resource_id}
  GET    /api/v1/status/incidents
  GET    /api/v1/status/incidents/{incident_id}
  GET    /api/v1/status/events
  GET    /api/v1/status/events/{event_id}
  POST   /api/v1/compute/job/{resource_id}            — submit (PSI/J JobSpec)
  POST   /api/v1/compute/status/{resource_id}         — list jobs
  GET    /api/v1/compute/status/{resource_id}/{id}    — job status
  PUT    /api/v1/compute/job/{resource_id}/{id}       — modify job
  DELETE /api/v1/compute/cancel/{resource_id}/{id}    — cancel job

There is NO filesystem API on this enclave (unlike ALCF IRI). Use Globus
Transfer for file movement; the OLCF DTN Globus endpoint UUID is
36d521b3-c182-4071-b7d5-91db5d380d42 (covers /ccs/home and Orion scratch).
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # mcp/ root
from auth_env import environ_token as _environ_token
from auth_env import local_globus_endpoint as _local_globus_endpoint

DEFAULT_BASE_URL = "https://amsc-moderate.s3m.olcf.ornl.gov"
GLOBUS_TRANSFER_BASE_URL = "https://transfer.api.globus.org/v0.10"

# OLCF DTN Globus endpoint (covers /ccs/home and Orion scratch)
OLCF_GLOBUS_ENDPOINT_IDS = {
    "olcf": "36d521b3-c182-4071-b7d5-91db5d380d42",
    "dtn": "36d521b3-c182-4071-b7d5-91db5d380d42",
    "frontier": "36d521b3-c182-4071-b7d5-91db5d380d42",
    "orion": "36d521b3-c182-4071-b7d5-91db5d380d42",
}


class OLCFIRIClient:
    """Thin async wrapper around the OLCF IRI REST API on amsc-moderate."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("OLCF_IRI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.token: str | None = _environ_token("olcf") or None
        self.transfer_token: str | None = os.environ.get("GLOBUS_TRANSFER_TOKEN")
        # Callables returning the CURRENT token from the per-user .env file, set
        # by the server so every request re-reads the freshest token instead of
        # a value cached at startup.
        self.token_provider = None
        self.transfer_token_provider = None
        self._client = httpx.AsyncClient(timeout=60.0)

    # -- auth -----------------------------------------------------------

    def set_token(self, token: str) -> None:
        self.token = token

    def _current_token(self) -> str | None:
        """Freshest OLCF token: provider (file) wins, else the cached value."""
        if self.token_provider:
            try:
                fresh = self.token_provider()
            except Exception:
                fresh = ""
            if fresh:
                self.token = fresh
        return self.token

    def _headers(self, auth_required: bool = True) -> dict[str, str]:
        # NOTE: Unlike ALCF IRI, every endpoint on amsc-moderate (including
        # /openapi.json's listed "public" status routes) requires Bearer auth.
        # Always send the token if we have one.
        headers = {"Accept": "application/json"}
        token = self._current_token()
        if token and auth_required:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # -- low-level request helpers -------------------------------------

    @staticmethod
    def _raise_with_body(resp) -> None:
        if resp.is_error:
            body = resp.text[:2000] if resp.text else "(empty body)"
            raise RuntimeError(f"HTTP {resp.status_code} for {resp.url} | body: {body}")

    async def _get(self, path: str, *, params: dict | None = None) -> dict | list:
        resp = await self._client.get(
            f"{self.base_url}/{path.lstrip('/')}",
            headers=self._headers(True),
            params=params,
        )
        self._raise_with_body(resp)
        return resp.json()

    async def _post(self, path: str, *, data: dict | None = None) -> dict | list:
        resp = await self._client.post(
            f"{self.base_url}/{path.lstrip('/')}",
            headers={**self._headers(True), "Content-Type": "application/json"},
            json=data,
        )
        self._raise_with_body(resp)
        return resp.json()

    async def _put(self, path: str, *, data: dict | None = None) -> dict | list:
        resp = await self._client.put(
            f"{self.base_url}/{path.lstrip('/')}",
            headers={**self._headers(True), "Content-Type": "application/json"},
            json=data,
        )
        self._raise_with_body(resp)
        return resp.json()

    async def _delete(self, path: str) -> int:
        resp = await self._client.delete(
            f"{self.base_url}/{path.lstrip('/')}",
            headers=self._headers(True),
        )
        self._raise_with_body(resp)
        return resp.status_code

    # -- facility ------------------------------------------------------

    async def get_facility(self) -> dict:
        return await self._get("api/v1/facility")

    async def list_sites(self) -> list:
        return await self._get("api/v1/facility/sites")

    async def get_site(self, site_id: str) -> dict:
        return await self._get(f"api/v1/facility/sites/{site_id}")

    # -- status --------------------------------------------------------

    async def list_resources(self) -> list:
        return await self._get("api/v1/status/resources")

    async def get_resource(self, resource_id: str) -> dict:
        return await self._get(f"api/v1/status/resources/{resource_id}")

    async def list_incidents(self, resource_id: str | None = None) -> list:
        params = {}
        if resource_id:
            params["resource_id"] = resource_id
        return await self._get("api/v1/status/incidents", params=params or None)

    async def get_incident(self, incident_id: str) -> dict:
        return await self._get(f"api/v1/status/incidents/{incident_id}")

    async def list_events(self) -> list:
        return await self._get("api/v1/status/events")

    async def get_event(self, event_id: str) -> dict:
        return await self._get(f"api/v1/status/events/{event_id}")

    # -- compute (PSI/J JobSpec) ---------------------------------------

    async def submit_job(self, resource_id: str, job_spec: dict) -> dict:
        """Submit a job. job_spec is a PSI/J JobSpec — see build_job_spec()."""
        return await self._post(f"api/v1/compute/job/{resource_id}", data=job_spec)

    async def get_job_status(self, resource_id: str, job_id: str) -> dict:
        return await self._get(f"api/v1/compute/status/{resource_id}/{job_id}")

    async def list_jobs(self, resource_id: str) -> list:
        # POST with empty body — same as ALCF IRI
        return await self._post(f"api/v1/compute/status/{resource_id}", data={})

    async def modify_job(self, resource_id: str, job_id: str, job_spec: dict) -> dict:
        return await self._put(f"api/v1/compute/job/{resource_id}/{job_id}", data=job_spec)

    async def cancel_job(self, resource_id: str, job_id: str) -> int:
        return await self._delete(f"api/v1/compute/cancel/{resource_id}/{job_id}")

    @staticmethod
    def build_job_spec(
        executable: str,
        *,
        arguments: list[str] | None = None,
        directory: str | None = None,
        name: str | None = None,
        environment: dict[str, str] | None = None,
        stdout_path: str | None = None,
        stderr_path: str | None = None,
        # Resources
        node_count: int | None = None,
        process_count: int | None = None,
        processes_per_node: int | None = None,
        cpu_cores_per_process: int | None = None,
        gpu_cores_per_process: int | None = None,
        exclusive_node_use: bool = True,
        # Attributes
        duration: int | None = None,
        queue_name: str | None = None,
        account: str | None = None,
        reservation_id: str | None = None,
        custom_attributes: dict[str, str] | None = None,
    ) -> dict:
        """Build a PSI/J JobSpec dict suitable for submit_job().

        Mirrors the JobSpec-Input schema from /openapi.json on amsc-moderate.
        """
        spec: dict = {"executable": executable}
        if arguments:        spec["arguments"] = arguments
        if directory:        spec["directory"] = directory
        if name:             spec["name"] = name
        if environment:      spec["environment"] = environment
        if stdout_path:      spec["stdout_path"] = stdout_path
        if stderr_path:      spec["stderr_path"] = stderr_path

        resources: dict = {}
        if node_count is not None:            resources["node_count"] = node_count
        if process_count is not None:         resources["process_count"] = process_count
        if processes_per_node is not None:    resources["processes_per_node"] = processes_per_node
        if cpu_cores_per_process is not None: resources["cpu_cores_per_process"] = cpu_cores_per_process
        if gpu_cores_per_process is not None: resources["gpu_cores_per_process"] = gpu_cores_per_process
        resources["exclusive_node_use"] = exclusive_node_use
        if resources:        spec["resources"] = resources

        attributes: dict = {}
        if duration is not None:          attributes["duration"] = duration
        if queue_name:                    attributes["queue_name"] = queue_name
        if account:                       attributes["account"] = account
        if reservation_id:                attributes["reservation_id"] = reservation_id
        if custom_attributes:             attributes["custom_attributes"] = custom_attributes
        if attributes:       spec["attributes"] = attributes

        return spec

    # -- Globus Transfer (filesystem ops live here, not in IRI) --------

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
            raise RuntimeError(
                "No Globus Transfer token. Call authenticate_globus_transfer() first."
            )
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _resolve_endpoint(self, name_or_id: str) -> str:
        key = name_or_id.lower()
        if key in OLCF_GLOBUS_ENDPOINT_IDS:
            return OLCF_GLOBUS_ENDPOINT_IDS[key]
        if len(name_or_id) == 36 and name_or_id.count("-") == 4:
            return name_or_id
        # Search Globus catalog as a fallback
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
            "DATA": [{
                "DATA_TYPE": "transfer_item",
                "source_path": source_path,
                "destination_path": dest_path,
                "recursive": recursive,
            }],
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
        /ccs/home/<user>/run1 (or an Orion scratch path) comes into being in one
        call. Use it to ensure a staging destination exists before globus_transfer.
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

    async def globus_wait_for_transfer(
        self, task_id: str, poll_interval: float = 3.0, max_wait: float = 300.0
    ) -> dict:
        elapsed = 0.0
        task: dict = {}
        while elapsed < max_wait:
            task = await self.globus_get_task(task_id)
            if task.get("status") in ("SUCCEEDED", "FAILED", "INACTIVE"):
                return task
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        return task

    def _local_endpoint(self) -> str:
        """UUID of this machine's Globus Connect Personal collection.

        Delegates to the one canonical autodetect in auth_env so all three
        facility clients resolve the local endpoint the same way.
        """
        return _local_globus_endpoint()

    async def upload_file(
        self, remote_path: str, content: str, endpoint: str = "olcf"
    ) -> dict:
        """Upload text content to a file on an OLCF filesystem via Globus.

        Writes content to a local temp file, transfers it to OLCF, then cleans up.
        Requires: authenticate_globus_transfer() + local Globus Connect Personal.
        """
        ep_id = await self._resolve_endpoint(endpoint)
        local_ep = self._local_endpoint()
        tmp_dir = Path.home() / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".olcf_upload", delete=False, dir=str(tmp_dir)
        ) as tmp:
            tmp.write(content)
            local_path = tmp.name
        try:
            task = await self.globus_transfer(
                source_endpoint=local_ep, source_path=local_path,
                dest_endpoint=ep_id, dest_path=remote_path,
                label=f"upload to {remote_path}",
            )
            result = await self.globus_wait_for_transfer(task["task_id"])
            if result.get("status") != "SUCCEEDED":
                raise RuntimeError(f"Globus transfer failed: {result.get('status')}")
            return {
                "status": "uploaded",
                "path": remote_path,
                "bytes": len(content.encode()),
                "task_id": task["task_id"],
            }
        finally:
            try:
                os.unlink(local_path)
            except Exception:
                pass

    async def download_file(
        self, remote_path: str, endpoint: str = "olcf", max_bytes: int = 5 * 1024 * 1024
    ) -> str:
        """Download a small file (<5 MB) from an OLCF filesystem via Globus."""
        ep_id = await self._resolve_endpoint(endpoint)
        local_ep = self._local_endpoint()
        tmp_dir = Path.home() / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            suffix=".olcf_download", delete=False, dir=str(tmp_dir)
        ) as tmp:
            local_path = tmp.name
        try:
            task = await self.globus_transfer(
                source_endpoint=ep_id, source_path=remote_path,
                dest_endpoint=local_ep, dest_path=local_path,
                label=f"OLCF IRI download {os.path.basename(remote_path)}",
            )
            result = await self.globus_wait_for_transfer(task["task_id"])
            if result.get("status") != "SUCCEEDED":
                raise RuntimeError(f"Globus transfer {result.get('status')}: {result.get('nice_status_details','')}")
            size = os.path.getsize(local_path)
            if size > max_bytes:
                raise RuntimeError(f"File too large ({size} bytes > {max_bytes}). Use globus_transfer() directly.")
            return Path(local_path).read_text(errors="replace")
        finally:
            try:
                os.unlink(local_path)
            except Exception:
                pass
