"""Background lag / health metrics collection."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class MetricsCollectionMixin:
    clients: Dict[str, Any]
    metrics_store: Any
    _shutdown_event: asyncio.Event
    _metrics_task: Optional[asyncio.Task]
    _last_metrics_cleanup_monotonic: float

    _METRICS_CONCURRENCY = 50

    async def _collect_single_deployment(self, deployment_name: str, client: Any) -> None:
        if self.metrics_store is None:
            return
        try:
            extracts = await client.list_extracts()
            for extract in extracts.get("items", []):
                extract_name = extract.get("name")
                if extract.get("status") == "running":
                    try:
                        lag_data = await client.get_extract_lag(extract_name)
                        await asyncio.to_thread(
                            self.metrics_store.record_lag_metric,
                            deployment_name,
                            extract_name,
                            "extract",
                            lag_data,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.debug("Could not collect Extract lag: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "Error collecting Extract metrics for %s: %s",
                deployment_name,
                e,
            )

        try:
            replicats = await client.list_replicats()
            for replicat in replicats.get("items", []):
                replicat_name = replicat.get("name")
                if replicat.get("status") == "running":
                    try:
                        lag_data = await client.get_replicat_lag(replicat_name)
                        await asyncio.to_thread(
                            self.metrics_store.record_lag_metric,
                            deployment_name,
                            replicat_name,
                            "replicat",
                            lag_data,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.debug("Could not collect Replicat lag: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "Error collecting Replicat metrics for %s: %s",
                deployment_name,
                e,
            )

        try:
            health = await self._get_deployment_health(deployment_name)
            await asyncio.to_thread(
                self.metrics_store.record_health_snapshot, deployment_name, health
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "Error collecting health snapshot for %s: %s",
                deployment_name,
                e,
            )

    async def _metrics_collection_round(self) -> None:
        sem = asyncio.Semaphore(self._METRICS_CONCURRENCY)

        async def _guarded(name: str, client: Any) -> None:
            if self._shutdown_event.is_set():
                return
            async with sem:
                await self._collect_single_deployment(name, client)

        await asyncio.gather(
            *(_guarded(name, client) for name, client in self.clients.items()),
            return_exceptions=True,
        )

        now_m = time.monotonic()
        if now_m - self._last_metrics_cleanup_monotonic >= 3600:
            await asyncio.to_thread(self.metrics_store.cleanup_old_data, retention_days=30)
            self._last_metrics_cleanup_monotonic = now_m

    def _start_metrics_collection(self) -> None:
        async def collect_metrics_loop():
            while not self._shutdown_event.is_set():
                try:
                    await self._metrics_collection_round()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error("Error in metrics collection loop: %s", e)

                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=300)
                except asyncio.TimeoutError:
                    pass

            logger.info("Metrics collection loop stopped")

        self._metrics_task = asyncio.create_task(collect_metrics_loop())
        logger.info("Background metrics collection started (5 minute interval)")
