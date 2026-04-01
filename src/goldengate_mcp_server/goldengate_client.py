"""Async HTTP client for the GoldenGate Microservices REST API."""

import logging
from typing import Any, Dict, Optional, Union
from urllib.parse import urljoin

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .cache import RateLimiter, SimpleCache
from .utils import parse_lag_duration

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
                return cached_response

        url = urljoin(self.base_url, endpoint)

        await self.rate_limiter.acquire()

        try:
            result = await self._execute_with_retries(
                method, url, data, params
            )

            if method == "GET" and use_cache:
                cache_key = f"{endpoint}:{params}"
                self.cache.set(cache_key, result)

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

    # ==================== Extract Operations ====================

    async def list_extracts(self) -> Dict[str, Any]:
        """List all Extract processes."""
        return await self._request("GET", "/services/v2/extracts")

    async def get_extract_status(self, extract_name: str) -> Dict[str, Any]:
        """
        Get status of a specific Extract.

        Args:
            extract_name: Name of the Extract process

        Returns:
            Extract status information
        """
        # Sanitize input
        extract_name = self._sanitize_name(extract_name)
        return await self._request("GET", f"/services/v2/extracts/{extract_name}")

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
        status = full_status.get("response", full_status)

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

        return await self._request(
            "PATCH",
            f"/services/adminsrvr/v2/extracts/{extract_name}",
            data={"status": "running"}
        )

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

        return await self._request(
            "PATCH",
            f"/services/adminsrvr/v2/extracts/{extract_name}",
            data={"status": "stopped"}
        )

    # ==================== Replicat Operations ====================

    async def list_replicats(self) -> Dict[str, Any]:
        """List all Replicat processes."""
        return await self._request("GET", "/services/v2/replicats")

    async def get_replicat_status(self, replicat_name: str) -> Dict[str, Any]:
        """
        Get status of a specific Replicat.

        Args:
            replicat_name: Name of the Replicat process

        Returns:
            Replicat status information
        """
        replicat_name = self._sanitize_name(replicat_name)
        return await self._request("GET", f"/services/v2/replicats/{replicat_name}")

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
        status = full_status.get("response", full_status)

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

        return await self._request(
            "PATCH",
            f"/services/adminsrvr/v2/replicats/{replicat_name}",
            data={"status": "running"}
        )

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

        return await self._request(
            "PATCH",
            f"/services/adminsrvr/v2/replicats/{replicat_name}",
            data={"status": "stopped"}
        )

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

        if process_type == "extract":
            stats_endpoint = f"/services/v2/extracts/{process_name}/statistics"
            detail_endpoint = f"/services/v2/extracts/{process_name}"
        elif process_type == "replicat":
            stats_endpoint = f"/services/v2/replicats/{process_name}/statistics"
            detail_endpoint = f"/services/v2/replicats/{process_name}"
        else:
            raise ValueError(f"Invalid process type: {process_type}")

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
        if self.deployment_name:
            return await self._request("GET", f"/services/{self.deployment_name}/adminsrvr/v2/trails")
        return await self._request("GET", "/services/v2/trails")

    async def get_trail_info(self, trail_name: str) -> Dict[str, Any]:
        """
        Get information about a specific trail.

        Args:
            trail_name: Name of the trail

        Returns:
            Trail information including size, sequence numbers
        """
        trail_name = self._sanitize_name(trail_name)
        if self.deployment_name:
            return await self._request("GET", f"/services/{self.deployment_name}/adminsrvr/v2/trails/{trail_name}")
        return await self._request("GET", f"/services/v2/trails/{trail_name}")

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

        if self.deployment_name:
            return await self._request(
                "POST",
                f"/services/{self.deployment_name}/adminsrvr/v2/trails/{trail_name}/commands/purge",
                data={"keepFiles": keep_files}
            )
        return await self._request(
            "POST",
            f"/services/v2/trails/{trail_name}/commands/purge",
            data={"keepFiles": keep_files}
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
        results = {}

        for name in process_names:
            try:
                if process_type == "extract":
                    result = await self.start_extract(name)
                else:
                    result = await self.start_replicat(name)
                results[name] = {"success": True, "result": result}
            except Exception as e:
                results[name] = {"success": False, "error": str(e)}
                logger.error(f"Failed to start {process_type} {name}: {e}")

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
        results = {}

        for name in process_names:
            try:
                if process_type == "extract":
                    result = await self.stop_extract(name)
                else:
                    result = await self.stop_replicat(name)
                results[name] = {"success": True, "result": result}
            except Exception as e:
                results[name] = {"success": False, "error": str(e)}
                logger.error(f"Failed to stop {process_type} {name}: {e}")

        return {
            "total": len(process_names),
            "successful": sum(1 for r in results.values() if r["success"]),
            "failed": sum(1 for r in results.values() if not r["success"]),
            "results": results
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

        # Get all extracts
        try:
            extracts_data = await self.list_extracts()
            # Handle standardResponse wrapper
            items = (
                extracts_data.get("response", {}).get("items", [])
                if "response" in extracts_data
                else extracts_data.get("items", [])
            )

            for extract in items:
                name = extract.get("name")
                status = extract.get("status")

                extract_info = {"name": name, "status": status}

                if status == "running":
                    try:
                        lag_data = await self.get_extract_lag(name)
                        extract_info["lag"] = lag_data

                        # Parse lag for summary
                        lag_str = lag_data.get("lag", "00:00:00")
                        lag_seconds = parse_lag_duration(lag_str)
                        if lag_seconds:
                            health["summary"]["total_lag_seconds"] += lag_seconds
                    except Exception:
                        pass

                health["extracts"].append(extract_info)
                health["summary"]["total_processes"] += 1

                if status == "running":
                    health["summary"]["running"] += 1
                elif status == "stopped":
                    health["summary"]["stopped"] += 1
                elif status == "abended":
                    health["summary"]["abended"] += 1
        except Exception as e:
            logger.error(f"Error getting extracts: {e}")

        # Get all replicats
        try:
            replicats_data = await self.list_replicats()
            # Handle standardResponse wrapper
            items = (
                replicats_data.get("response", {}).get("items", [])
                if "response" in replicats_data
                else replicats_data.get("items", [])
            )

            for replicat in items:
                name = replicat.get("name")
                status = replicat.get("status")

                replicat_info = {"name": name, "status": status}

                if status == "running":
                    try:
                        lag_data = await self.get_replicat_lag(name)
                        replicat_info["lag"] = lag_data

                        lag_str = lag_data.get("lag", "00:00:00")
                        lag_seconds = parse_lag_duration(lag_str)
                        if lag_seconds:
                            health["summary"]["total_lag_seconds"] += lag_seconds
                    except Exception:
                        pass

                health["replicats"].append(replicat_info)
                health["summary"]["total_processes"] += 1

                if status == "running":
                    health["summary"]["running"] += 1
                elif status == "stopped":
                    health["summary"]["stopped"] += 1
                elif status == "abended":
                    health["summary"]["abended"] += 1
        except Exception as e:
            logger.error(f"Error getting replicats: {e}")

        return health

    # ==================== Configuration Management ====================

    async def get_extract_config(self, extract_name: str) -> Dict[str, Any]:
        """
        Get Extract parameter file configuration.

        Args:
            extract_name: Name of the Extract process

        Returns:
            Configuration including parameter file content
        """
        extract_name = self._sanitize_name(extract_name)

        # Get full extract details which includes configuration
        extract_details = await self._request(
            "GET",
            f"/services/v2/extracts/{extract_name}"
        )

        # Try to get parameter file content
        try:
            param_file = await self._request(
                "GET",
                f"/services/v2/extracts/{extract_name}/parameterfile"
            )
            extract_details["parameter_file"] = param_file
        except Exception as e:
            logger.debug(f"Could not get parameter file for {extract_name}: {e}")
            extract_details["parameter_file"] = None

        return extract_details

    async def get_replicat_config(self, replicat_name: str) -> Dict[str, Any]:
        """
        Get Replicat parameter file configuration.

        Args:
            replicat_name: Name of the Replicat process

        Returns:
            Configuration including parameter file content
        """
        replicat_name = self._sanitize_name(replicat_name)

        # Get full replicat details
        replicat_details = await self._request(
            "GET",
            f"/services/v2/replicats/{replicat_name}"
        )

        # Try to get parameter file content
        try:
            param_file = await self._request(
                "GET",
                f"/services/v2/replicats/{replicat_name}/parameterfile"
            )
            replicat_details["parameter_file"] = param_file
        except Exception as e:
            logger.debug(f"Could not get parameter file for {replicat_name}: {e}")
            replicat_details["parameter_file"] = None

        return replicat_details

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

        # Backup all extracts
        try:
            extracts = await self.list_extracts()
            for extract in extracts.get("items", []):
                name = extract.get("name")
                try:
                    config = await self.get_extract_config(name)
                    backup["extracts"][name] = config
                except Exception as e:
                    logger.error(f"Failed to backup Extract {name}: {e}")
                    backup["extracts"][name] = {"error": str(e)}
        except Exception as e:
            logger.error(f"Failed to list extracts: {e}")

        # Backup all replicats
        try:
            replicats = await self.list_replicats()
            for replicat in replicats.get("items", []):
                name = replicat.get("name")
                try:
                    config = await self.get_replicat_config(name)
                    backup["replicats"][name] = config
                except Exception as e:
                    logger.error(f"Failed to backup Replicat {name}: {e}")
                    backup["replicats"][name] = {"error": str(e)}
        except Exception as e:
            logger.error(f"Failed to list replicats: {e}")

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

        # Compare extracts
        extracts1 = set(config1.get("extracts", {}).keys())
        extracts2 = set(config2.get("extracts", {}).keys())

        added_extracts = extracts1 - extracts2
        removed_extracts = extracts2 - extracts1
        common_extracts = extracts1 & extracts2

        if added_extracts:
            comparison["identical"] = False
            comparison["summary"]["added_processes"].extend(
                [f"Extract: {name}" for name in added_extracts]
            )

        if removed_extracts:
            comparison["identical"] = False
            comparison["summary"]["removed_processes"].extend(
                [f"Extract: {name}" for name in removed_extracts]
            )

        # Compare common extracts
        for name in common_extracts:
            ext1 = config1["extracts"][name]
            ext2 = config2["extracts"][name]

            # Compare parameter files
            param1 = ext1.get("parameter_file", {})
            param2 = ext2.get("parameter_file", {})

            if param1 != param2:
                comparison["identical"] = False
                comparison["summary"]["modified_processes"].append(f"Extract: {name}")
                comparison["differences"].append({
                    "process": name,
                    "type": "extract",
                    "field": "parameter_file",
                    "change": "modified"
                })

        # Compare replicats
        replicats1 = set(config1.get("replicats", {}).keys())
        replicats2 = set(config2.get("replicats", {}).keys())

        added_replicats = replicats1 - replicats2
        removed_replicats = replicats2 - replicats1
        common_replicats = replicats1 & replicats2

        if added_replicats:
            comparison["identical"] = False
            comparison["summary"]["added_processes"].extend(
                [f"Replicat: {name}" for name in added_replicats]
            )

        if removed_replicats:
            comparison["identical"] = False
            comparison["summary"]["removed_processes"].extend(
                [f"Replicat: {name}" for name in removed_replicats]
            )

        # Compare common replicats
        for name in common_replicats:
            rep1 = config1["replicats"][name]
            rep2 = config2["replicats"][name]

            param1 = rep1.get("parameter_file", {})
            param2 = rep2.get("parameter_file", {})

            if param1 != param2:
                comparison["identical"] = False
                comparison["summary"]["modified_processes"].append(f"Replicat: {name}")
                comparison["differences"].append({
                    "process": name,
                    "type": "replicat",
                    "field": "parameter_file",
                    "change": "modified"
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
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            raise ValueError(f"Invalid process name: {name}")
        return name
