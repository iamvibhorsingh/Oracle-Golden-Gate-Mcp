"""Async HTTP client for the GoldenGate Microservices REST API."""

import asyncio
import copy
import difflib
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urljoin

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .cache import RateLimiter, SimpleCache
from .redaction import redact_error_text
from .utils import collection_items, parse_lag_duration, unwrap_response

logger = logging.getLogger(__name__)


class GoldenGateAPIError(Exception):
    """Base exception for GoldenGate API errors."""

    def __init__(self, message: str, *, http_status: Optional[int] = None):
        super().__init__(message)
        self.http_status = http_status


def _is_transient_request_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, GoldenGateAPIError):
        return exc.http_status in (502, 503, 504)
    return False


async def process_states(client: Any, process_type: str) -> List[Dict[str, Any]]:
    """
    ``{"name", "status"}`` for every Extract or Replicat in a deployment.

    The list endpoints may return only names and links, so a missing status is filled in
    from the process detail (``"unknown"`` if that fails) rather than reported as healthy.
    """
    if process_type == "extract":
        listing, detail = client.list_extracts, client.get_extract_status
    else:
        listing, detail = client.list_replicats, client.get_replicat_status

    async def state(item: Dict[str, Any]) -> Dict[str, Any]:
        name, status = item.get("name"), item.get("status")
        if status is None:
            try:
                status = unwrap_response(await detail(name)).get("status")
            except Exception as e:
                logger.warning("Could not get status of %s %s: %s", process_type, name, e)
        return {"name": name, "status": status or "unknown"}

    return list(await asyncio.gather(*(state(i) for i in collection_items(await listing()))))


def _param_lines(process: Any) -> List[str]:
    """Parameter file of a backed-up process as comparable lines."""
    param = process.get("parameter_file") if isinstance(process, dict) else None
    if isinstance(param, str):
        return param.splitlines()
    if isinstance(param, dict) and isinstance(param.get("lines"), list):
        return [str(line) for line in param["lines"]]
    if not param:
        return []
    return json.dumps(param, indent=2, sort_keys=True, default=str).splitlines()


class GoldenGateClient:
    """GoldenGate Microservices REST API client (GG 21.x / 23.x)."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
        ca_bundle: Optional[str] = None,
        timeout: int = 30,
        cache_ttl: int = 30,
        max_concurrent: int = 10,
        requests_per_second: int = 20,
        deployment_name: str = ""
    ):
        self.base_url = base_url.rstrip('/')
        self.deployment_name = deployment_name
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout

        self.cache = SimpleCache(ttl_seconds=cache_ttl)
        self.rate_limiter = RateLimiter(
            max_concurrent=max_concurrent,
            requests_per_second=requests_per_second
        )

        # verify= accepts: True (system CAs), False (skip), or a path string (custom CA bundle)
        if ca_bundle:
            ssl_verify: Union[bool, str] = ca_bundle
            logger.info(f"Using custom CA bundle for {base_url}: {ca_bundle}")
        else:
            ssl_verify = verify_ssl

        self.client = httpx.AsyncClient(
            auth=(username, password),
            verify=ssl_verify,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
        )

        if not verify_ssl and not ca_bundle:
            logger.warning(f"SSL verification disabled for {base_url}")

    def _admin_path(self, suffix: str) -> str:
        """Administration Server path for this deployment.

        GoldenGate routes by deployment name (``/services/{deployment}/adminsrvr/v2/...``),
        so ``deployment_name`` must be the real GoldenGate deployment name.
        """
        if not self.deployment_name:
            raise ValueError("GoldenGateClient requires deployment_name for adminsrvr calls")
        return f"/services/{self.deployment_name}/adminsrvr/v2/{suffix}"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        if method == "GET" and use_cache:
            cache_key = f"{endpoint}:{params}"
            cached_response = self.cache.get(cache_key)
            if cached_response is not None:
                return copy.deepcopy(cached_response)

        url = urljoin(self.base_url, endpoint)

        await self.rate_limiter.acquire()

        try:
            result = await self._execute_with_retries(
                method, url, data, params
            )

            if method == "GET" and use_cache:
                cache_key = f"{endpoint}:{params}"
                self.cache.set(cache_key, copy.deepcopy(result))

            return result
        except httpx.HTTPError as e:
            logger.error("HTTP error: %s", e)
            raise GoldenGateAPIError(f"Connection error: {str(e)}") from e
        except GoldenGateAPIError:
            raise
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            raise GoldenGateAPIError(f"Unexpected error: {str(e)}") from e
        finally:
            self.rate_limiter.release()
            if method != "GET":
                # Any write can change process status/lag; drop cached reads so the
                # next status call reflects it instead of serving a stale entry.
                self.cache.clear()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception(_is_transient_request_error),
        reraise=True,
    )
    async def _execute_with_retries(
        self,
        method: str,
        url: str,
        data: Optional[Dict[str, Any]],
        params: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        logger.debug("%s %s", method, url)

        response = await self.client.request(
            method=method,
            url=url,
            json=data,
            params=params,
        )

        if response.status_code >= 400:
            error_detail = ""
            try:
                error_data = response.json()
                error_detail = error_data.get("error", error_data.get("message", ""))
            except Exception:
                error_detail = response.text

            raise GoldenGateAPIError(
                f"API error {response.status_code}: {error_detail}",
                http_status=response.status_code,
            )

        if response.status_code == 204:
            return {"success": True, "message": "Operation completed successfully"}
        return response.json() if response.text else {}

    # ==================== Deployment Operations ====================

    async def get_deployment_info(self) -> Dict[str, Any]:
        """Get deployment information."""
        return await self._request("GET", "/services/v2/deployments")

    async def list_services(self) -> Dict[str, Any]:
        """List all services in the deployment."""
        return await self._request("GET", "/services")

    # ==================== Process Runtime Information ====================

    def _info_path(self, process_type: str, process_name: str, suffix: str) -> str:
        if process_type not in ("extract", "replicat"):
            raise ValueError(f"Invalid process type: {process_type}")
        process_name = self._sanitize_name(process_name)
        return self._admin_path(f"{process_type}s/{process_name}/info/{suffix}")

    async def _with_runtime_lag(
        self, process_type: str, process_name: str, full_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process detail body, with ``lag`` from /info/status when the detail has none."""
        status = unwrap_response(full_status)
        if status.get("lag") is not None or status.get("status") != "running":
            return status
        try:
            runtime = await self._request(
                "GET", self._info_path(process_type, process_name, "status")
            )
        except GoldenGateAPIError as e:
            logger.debug("No /info/status for %s: %s", process_name, e)
            return status
        return {**status, **unwrap_response(runtime)}

    async def get_process_report(
        self, process_type: str, process_name: str, report: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a process report file (the current one unless ``report`` names another).

        Args:
            process_type: 'extract' or 'replicat'
            process_name: Name of the process
            report: Report file, e.g. 'EXT10.rpt' for the previous run

        Returns:
            Report body with its ``lines``
        """
        name = report or f"{process_name}.rpt"
        if not re.fullmatch(r"[a-zA-Z0-9_-]+\.rpt", name):
            raise ValueError(f"Invalid report name: {name}")
        path = self._info_path(process_type, process_name, f"reports/{name}")
        return unwrap_response(await self._request("GET", path, use_cache=False))

    async def get_process_checkpoints(
        self, process_type: str, process_name: str, history: int = 0
    ) -> Dict[str, Any]:
        """
        Get a process's checkpoint positions.

        Args:
            process_type: 'extract' or 'replicat'
            process_name: Name of the process
            history: Number of historical checkpoint records to include

        Returns:
            Checkpoint information
        """
        path = self._info_path(process_type, process_name, "checkpoints")
        params = {"history": history} if history else None
        return unwrap_response(await self._request("GET", path, params=params))

    async def get_messages(self) -> Dict[str, Any]:
        """Get recent messages from the Administration Server."""
        return unwrap_response(
            await self._request("GET", self._admin_path("messages"), use_cache=False)
        )

    # ==================== Extract Operations ====================

    async def list_extracts(self) -> Dict[str, Any]:
        """List all Extract processes."""
        return await self._request("GET", self._admin_path("extracts"))

    async def get_extract_status(
        self, extract_name: str, use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        Get status of a specific Extract.

        Args:
            extract_name: Name of the Extract process

        Returns:
            Extract status information
        """
        # Sanitize input
        extract_name = self._sanitize_name(extract_name)
        return await self._request(
            "GET", self._admin_path(f"extracts/{extract_name}"), use_cache=use_cache
        )

    async def get_extract_lag(self, extract_name: str) -> Dict[str, Any]:
        """
        Get lag information for an Extract.

        Args:
            extract_name: Name of the Extract process

        Returns:
            Lag information
        """
        extract_name = self._sanitize_name(extract_name)

        # Get full status which includes lag
        full_status = await self.get_extract_status(extract_name)
        status = await self._with_runtime_lag("extract", extract_name, full_status)

        # Extract lag information
        lag_data = {
            "process": extract_name,
            "type": "extract",
            "lag_at_chkpt": status.get("lagAtChkpt"),
            "time_since_chkpt": status.get("timeSinceChkpt"),
            "lag": status.get("lag"),
            "status": status.get("status")
        }

        return lag_data

    async def start_extract(self, extract_name: str) -> Dict[str, Any]:
        """
        Start an Extract process.

        Args:
            extract_name: Name of the Extract process

        Returns:
            Operation result
        """
        extract_name = self._sanitize_name(extract_name)
        logger.info(f"Starting Extract: {extract_name}")

        path = self._admin_path(f"extracts/{extract_name}")
        return await self._request("PATCH", path, data={"status": "running"})

    async def stop_extract(self, extract_name: str) -> Dict[str, Any]:
        """
        Stop an Extract process.

        Args:
            extract_name: Name of the Extract process

        Returns:
            Operation result
        """
        extract_name = self._sanitize_name(extract_name)
        logger.info(f"Stopping Extract: {extract_name}")

        path = self._admin_path(f"extracts/{extract_name}")
        return await self._request("PATCH", path, data={"status": "stopped"})

    # ==================== Replicat Operations ====================

    async def list_replicats(self) -> Dict[str, Any]:
        """List all Replicat processes."""
        return await self._request("GET", self._admin_path("replicats"))

    async def get_replicat_status(
        self, replicat_name: str, use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        Get status of a specific Replicat.

        Args:
            replicat_name: Name of the Replicat process

        Returns:
            Replicat status information
        """
        replicat_name = self._sanitize_name(replicat_name)
        return await self._request(
            "GET", self._admin_path(f"replicats/{replicat_name}"), use_cache=use_cache
        )

    async def get_replicat_lag(self, replicat_name: str) -> Dict[str, Any]:
        """
        Get lag information for a Replicat.

        Args:
            replicat_name: Name of the Replicat process

        Returns:
            Lag information
        """
        replicat_name = self._sanitize_name(replicat_name)

        # Get full status which includes lag
        full_status = await self.get_replicat_status(replicat_name)
        status = await self._with_runtime_lag("replicat", replicat_name, full_status)

        # Extract lag information
        lag_data = {
            "process": replicat_name,
            "type": "replicat",
            "lag_at_chkpt": status.get("lagAtChkpt"),
            "time_since_chkpt": status.get("timeSinceChkpt"),
            "lag": status.get("lag"),
            "status": status.get("status")
        }

        return lag_data

    async def start_replicat(self, replicat_name: str) -> Dict[str, Any]:
        """
        Start a Replicat process.

        Args:
            replicat_name: Name of the Replicat process

        Returns:
            Operation result
        """
        replicat_name = self._sanitize_name(replicat_name)
        logger.info(f"Starting Replicat: {replicat_name}")

        path = self._admin_path(f"replicats/{replicat_name}")
        return await self._request("PATCH", path, data={"status": "running"})

    async def stop_replicat(self, replicat_name: str) -> Dict[str, Any]:
        """
        Stop a Replicat process.

        Args:
            replicat_name: Name of the Replicat process

        Returns:
            Operation result
        """
        replicat_name = self._sanitize_name(replicat_name)
        logger.info(f"Stopping Replicat: {replicat_name}")

        path = self._admin_path(f"replicats/{replicat_name}")
        return await self._request("PATCH", path, data={"status": "stopped"})

    # ==================== Statistics and Monitoring ====================

    async def get_process_statistics(
        self,
        process_type: str,
        process_name: str
    ) -> Dict[str, Any]:
        """
        Get statistics for a process.

        Args:
            process_type: Type of process ('extract' or 'replicat')
            process_name: Name of the process

        Returns:
            Process statistics
        """
        process_name = self._sanitize_name(process_name)

        if process_type not in ("extract", "replicat"):
            raise ValueError(f"Invalid process type: {process_type}")
        detail_endpoint = self._admin_path(f"{process_type}s/{process_name}")
        stats_endpoint = f"{detail_endpoint}/statistics"

        # Try the dedicated /statistics endpoint first (available in Enterprise editions).
        # Fall back to the process detail endpoint for GoldenGate Free, which returns
        # config, trail position, and status instead of throughput counters.
        try:
            result = await self._request("GET", stats_endpoint)
            result = result.get("response", result)
            result["_source"] = "statistics"
            return result
        except GoldenGateAPIError as e:
            if e.http_status == 404:
                logger.debug(
                    "Statistics endpoint not available for %s %s (likely Free Edition), "
                    "falling back to process detail.",
                    process_type, process_name,
                )
                result = await self._request("GET", detail_endpoint)
                result = result.get("response", result)
                result["_source"] = "detail_fallback"
                return result
            raise

    async def check_process_errors(
        self,
        process_type: str,
        process_name: str
    ) -> Dict[str, Any]:
        """
        Check for errors in a process.

        Args:
            process_type: Type of process ('extract' or 'replicat')
            process_name: Name of the process

        Returns:
            Error information
        """
        process_name = self._sanitize_name(process_name)

        # Get process status which includes error info
        if process_type == "extract":
            full_status = await self.get_extract_status(process_name)
        elif process_type == "replicat":
            full_status = await self.get_replicat_status(process_name)
        else:
            raise ValueError(f"Invalid process type: {process_type}")

        status = full_status.get("response", full_status)

        # Extract error information
        error_info = {
            "process": process_name,
            "type": process_type,
            "status": status.get("status"),
            "has_errors": status.get("status") in ["abended", "error"],
            "messages": []
        }

        # Get messages if available
        if "messages" in status:
            error_info["messages"] = status["messages"]

        return error_info

    # ==================== Trail Operations ====================

    async def list_trails(self) -> Dict[str, Any]:
        """List all trail files."""
        return await self._request("GET", self._admin_path("trails"))

    async def get_trail_info(
        self, trail_name: str, trail_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get information about a specific trail.

        Args:
            trail_name: Name of the trail (e.g. 'lt', 'rt')
            trail_path: Optional path qualifier passed as ?path= query parameter

        Returns:
            Trail information including size, sequence numbers
        """
        trail_name = self._sanitize_name(trail_name)
        params = {"path": trail_path} if trail_path else None
        return await self._request(
            "GET", self._admin_path(f"trails/{trail_name}"), params=params
        )

    async def purge_trail(self, trail_name: str, keep_files: int = 2) -> Dict[str, Any]:
        """
        Purge old trail files.

        Args:
            trail_name: Name of the trail
            keep_files: Number of recent files to keep

        Returns:
            Purge operation result
        """
        trail_name = self._sanitize_name(trail_name)
        logger.info(f"Purging trail {trail_name}, keeping {keep_files} files")

        return await self._request(
            "POST",
            self._admin_path(f"trails/{trail_name}/commands/purge"),
            data={"keepFiles": keep_files},
        )

    # ==================== Batch Operations ====================

    async def batch_start_processes(
        self,
        process_names: list[str],
        process_type: str
    ) -> Dict[str, Any]:
        """
        Start multiple processes at once.

        Args:
            process_names: List of process names
            process_type: 'extract' or 'replicat'

        Returns:
            Results for each process
        """
        action = self.start_extract if process_type == "extract" else self.start_replicat
        results = await self._run_batch(action, process_names, f"start {process_type}")

        return {
            "total": len(process_names),
            "successful": sum(1 for r in results.values() if r["success"]),
            "failed": sum(1 for r in results.values() if not r["success"]),
            "results": results
        }

    async def batch_stop_processes(
        self,
        process_names: list[str],
        process_type: str
    ) -> Dict[str, Any]:
        """
        Stop multiple processes at once.

        Args:
            process_names: List of process names
            process_type: 'extract' or 'replicat'

        Returns:
            Results for each process
        """
        action = self.stop_extract if process_type == "extract" else self.stop_replicat
        results = await self._run_batch(action, process_names, f"stop {process_type}")

        return {
            "total": len(process_names),
            "successful": sum(1 for r in results.values() if r["success"]),
            "failed": sum(1 for r in results.values() if not r["success"]),
            "results": results
        }

    async def _run_batch(self, action, process_names: list[str], label: str) -> Dict[str, Any]:
        """Run ``action`` for every name concurrently (bounded by the rate limiter)."""

        async def one(name: str) -> Dict[str, Any]:
            try:
                return {"success": True, "result": await action(name)}
            except Exception as e:
                logger.error("Failed to %s %s: %s", label, name, e)
                return {"success": False, "error": redact_error_text(str(e))}

        outcomes = await asyncio.gather(*(one(n) for n in process_names))
        return dict(zip(process_names, outcomes, strict=True))

    async def wait_for_status(
        self,
        process_type: str,
        process_name: str,
        wanted: str,
        timeout: float = 60,
        interval: float = 2,
    ) -> Optional[str]:
        """Poll live status until it is ``wanted`` or ``timeout`` passes; returns the last seen."""
        read = self.get_extract_status if process_type == "extract" else self.get_replicat_status
        deadline = time.monotonic() + timeout
        while True:
            status = unwrap_response(await read(process_name, use_cache=False)).get("status")
            if status == wanted or time.monotonic() >= deadline:
                return status
            await asyncio.sleep(interval)

    async def restart_process(
        self, process_type: str, process_name: str, timeout: float = 120
    ) -> Dict[str, Any]:
        """
        Stop a process, wait until it has stopped, start it and wait until it runs.

        A process that does not stop in time is left alone rather than started over itself.

        Args:
            process_type: 'extract' or 'replicat'
            process_name: Name of the process
            timeout: Seconds to wait for each transition

        Returns:
            Previous and final status; ``success`` is False if it is not running at the end
        """
        if process_type == "extract":
            read, start, stop = self.get_extract_status, self.start_extract, self.stop_extract
        else:
            read, start, stop = self.get_replicat_status, self.start_replicat, self.stop_replicat

        previous = unwrap_response(await read(process_name, use_cache=False)).get("status")
        if previous in ("running", "starting"):
            await stop(process_name)
            stopped = await self.wait_for_status(process_type, process_name, "stopped", timeout)
            if stopped != "stopped":
                return {
                    "success": False,
                    "error": f"{process_name} did not stop within {timeout}s; not restarted",
                    "previous_status": previous,
                    "status": stopped,
                }

        await start(process_name)
        status = await self.wait_for_status(process_type, process_name, "running", timeout)
        return {
            "success": status == "running",
            "previous_status": previous,
            "status": status,
        }

    async def get_all_process_health(self) -> Dict[str, Any]:
        """
        Get health status of all processes in one call.

        Returns:
            Comprehensive health summary
        """
        health = {
            "extracts": [],
            "replicats": [],
            "summary": {
                "total_processes": 0,
                "running": 0,
                "stopped": 0,
                "abended": 0,
                "total_lag_seconds": 0
            }
        }

        for process_type, group, get_lag in (
            ("extract", "extracts", self.get_extract_lag),
            ("replicat", "replicats", self.get_replicat_lag),
        ):
            try:
                for info in await process_states(self, process_type):
                    status = info["status"]

                    if status == "running":
                        try:
                            lag_data = await get_lag(info["name"])
                            info["lag"] = lag_data

                            # Parse lag for summary
                            lag_seconds = parse_lag_duration(lag_data.get("lag"))
                            if lag_seconds:
                                health["summary"]["total_lag_seconds"] += lag_seconds
                        except Exception:
                            pass

                    health[group].append(info)
                    health["summary"]["total_processes"] += 1

                    if status in ("running", "stopped", "abended"):
                        health["summary"][status] += 1
            except Exception as e:
                logger.error(f"Error getting {group}: {e}")

        return health

    # ==================== Configuration Management ====================

    @staticmethod
    def _with_parameter_file(details: Dict[str, Any]) -> Dict[str, Any]:
        """Expose a process's ``config`` lines (its parameter file) as ``parameter_file``."""
        lines = unwrap_response(details).get("config")
        details["parameter_file"] = {"lines": lines} if lines is not None else None
        return details

    async def get_extract_config(self, extract_name: str) -> Dict[str, Any]:
        """
        Get Extract parameter file configuration.

        Args:
            extract_name: Name of the Extract process

        Returns:
            Configuration including parameter file content
        """
        extract_name = self._sanitize_name(extract_name)
        details = await self._request("GET", self._admin_path(f"extracts/{extract_name}"))
        return self._with_parameter_file(details)

    async def get_replicat_config(self, replicat_name: str) -> Dict[str, Any]:
        """
        Get Replicat parameter file configuration.

        Args:
            replicat_name: Name of the Replicat process

        Returns:
            Configuration including parameter file content
        """
        replicat_name = self._sanitize_name(replicat_name)
        details = await self._request("GET", self._admin_path(f"replicats/{replicat_name}"))
        return self._with_parameter_file(details)

    async def backup_all_configs(self) -> Dict[str, Any]:
        """
        Backup all process configurations in the deployment.

        Returns:
            Complete configuration backup including all processes
        """
        backup = {
            "timestamp": None,
            "deployment_url": self.base_url,
            "extracts": {},
            "replicats": {},
            "deployment_info": None
        }

        from datetime import datetime
        backup["timestamp"] = datetime.utcnow().isoformat()

        # Get deployment info
        try:
            backup["deployment_info"] = await self.get_deployment_info()
        except Exception as e:
            logger.warning(f"Could not get deployment info: {e}")

        for process_type, group, get_config in (
            ("extract", "extracts", self.get_extract_config),
            ("replicat", "replicats", self.get_replicat_config),
        ):
            try:
                states = await process_states(self, process_type)
            except Exception as e:
                logger.error(f"Failed to list {group}: {e}")
                backup.setdefault("errors", []).append(
                    redact_error_text(f"Failed to list {group}: {e}")
                )
                continue
            for state in states:
                name = state["name"]
                try:
                    backup[group][name] = await get_config(name)
                except Exception as e:
                    logger.error(f"Failed to backup {process_type} {name}: {e}")
                    backup[group][name] = {"error": redact_error_text(str(e))}

        return backup

    @staticmethod
    def compare_configs(config1: Dict[str, Any], config2: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compare two configurations and identify differences.

        Args:
            config1: First configuration (e.g., current)
            config2: Second configuration (e.g., backup)

        Returns:
            Comparison results with differences highlighted
        """
        comparison = {
            "identical": True,
            "differences": [],
            "summary": {
                "added_processes": [],
                "removed_processes": [],
                "modified_processes": []
            }
        }

        for label, group in (("Extract", "extracts"), ("Replicat", "replicats")):
            current = config1.get(group, {})
            baseline = config2.get(group, {})

            added = sorted(set(current) - set(baseline))
            removed = sorted(set(baseline) - set(current))

            if added or removed:
                comparison["identical"] = False
            comparison["summary"]["added_processes"].extend(f"{label}: {n}" for n in added)
            comparison["summary"]["removed_processes"].extend(f"{label}: {n}" for n in removed)

            for name in sorted(set(current) & set(baseline)):
                lines_now = _param_lines(current[name])
                lines_then = _param_lines(baseline[name])

                if lines_now != lines_then:
                    comparison["identical"] = False
                    comparison["summary"]["modified_processes"].append(f"{label}: {name}")
                    comparison["differences"].append({
                        "process": name,
                        "type": label.lower(),
                        "field": "parameter_file",
                        "change": "modified",
                        "diff": list(difflib.unified_diff(
                            lines_then, lines_now, "baseline", "current", lineterm=""
                        )),
                    })

        return comparison

    # ==================== Connection Management ====================

    async def close(self):
        await self.client.aclose()
        logger.debug(f"Closed connection to {self.base_url}")

    # ==================== Utility Methods ====================

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """
        Sanitize a process name to prevent injection attacks.

        Args:
            name: Process name to sanitize

        Returns:
            Sanitized name
        """
        # Remove potentially dangerous characters
        # GoldenGate names are typically alphanumeric with underscores
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', name):
            raise ValueError(f"Invalid process name: {name}")
        return name
