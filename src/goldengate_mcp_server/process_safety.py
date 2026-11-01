"""Evidence-based checks before a GoldenGate process is started.

Blockers are limited to starts that are known to be unsafe: above all an initial-load
Replicat whose load did not finish cleanly, which would apply a partial load to the target.
Anything merely unverifiable is a warning, because normal setups can't always be proven from
one deployment: a Replicat's trail is often delivered by a distribution path from elsewhere.
"""

from __future__ import annotations

import asyncio
import posixpath
import re
from typing import Any, Dict, List, Optional, Set

from .goldengate_client import GoldenGateAPIError
from .redaction import redact_error_text
from .utils import collection_items, normalize_status, parse_ogg_messages, unwrap_response

MAX_INSPECTED_EXTRACTS = 128

_FAILURE_INDICATOR = re.compile(
    r"\b(ABENDED|ABENDING|TERMINATED\s+ABNORMALLY|FATAL|FAILED\s+TO)\b", re.IGNORECASE
)
_OPERATOR_STOP = re.compile(r"OGG-00990|Command received[^\r\n]*:\s*(?:STOP|KILL)\b", re.IGNORECASE)
_SPECIALRUN = re.compile(r"^\s*SPECIALRUN\b", re.IGNORECASE | re.MULTILINE)


def _file_reference(value: str) -> str:
    # Whole paths, not basenames: the same file name in two directories is two files.
    return posixpath.normpath(value.replace("\\", "/"))


def _config_text(details: Dict[str, Any]) -> str:
    lines = details.get("config")
    if not isinstance(lines, list):
        return ""
    return "\n".join(line for line in lines if isinstance(line, str))


def _parameter_files(config: str, parameter: str) -> Set[str]:
    """File arguments of every ``parameter`` line (EXTFILE, EXTTRAIL) in a parameter file."""
    matches = re.finditer(
        rf"^\s*{parameter}\s+(?:\"([^\"]+)\"|'([^']+)'|([^,\s]+))\s*(?:,|$)",
        config,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return {_file_reference(next(g for g in m.groups() if g)) for m in matches}


def _source_file(details: Dict[str, Any]) -> Optional[str]:
    """The file or trail a Replicat reads, if its detail names one."""
    source = details.get("source")
    if not isinstance(source, dict):
        return None
    name = source.get("filename") or source.get("name")
    path = source.get("path") or ""
    if not isinstance(name, str) or not name.strip() or not isinstance(path, str):
        return None
    return _file_reference(posixpath.join(path, name))


def _reads_transaction_logs(details: Dict[str, Any]) -> bool:
    source = details.get("source")
    return source == "tranlogs" or (isinstance(source, dict) and bool(source.get("tranlogs")))


class _Report:
    def __init__(self, process_type: str, name: str):
        self.data: Dict[str, Any] = {
            "process": name,
            "process_type": process_type,
            "status": None,
            "blockers": [],
            "warnings": [],
        }

    def block(self, code: str, message: str) -> None:
        self.data["blockers"].append({"code": code, "message": message})

    def warn(self, code: str, message: str) -> None:
        self.data["warnings"].append({"code": code, "message": message})


async def _check_initial_load_extract(client: Any, extract: str, report: _Report) -> None:
    """Block unless ``extract``'s latest run demonstrably finished its load."""

    async def runtime() -> Dict[str, Any]:
        return unwrap_response(await client.get_process_runtime_status("extract", extract))

    before = await runtime()
    if normalize_status(before.get("status")) != "stopped":
        report.block(
            "initial_load_extract_not_stopped",
            f"Initial Load Extract {extract} is {before.get('status')}; wait for it to finish",
        )
        return

    process_id = before.get("processId")
    try:
        history = (await client.get_process_history("extract", extract)).get("history")
    except GoldenGateAPIError as e:
        if e.http_status != 404:
            raise
        report.warn("history_unavailable", "Execution history is not available on this version")
    else:
        runs = [r for r in history or [] if isinstance(r, dict)]
        matching = [r for r in runs if r.get("processId") == process_id]
        if not matching:
            report.warn("history_unavailable", f"No execution history found for {extract}")
        # History is listed per process id; the newest start is the run in question.
        elif (run := max(matching, key=lambda r: str(r.get("started")))).get("exitCode") != 0 or (
            run.get("killedManually")
        ):
            report.block(
                "initial_load_run_failed",
                f"Latest run of {extract} did not exit cleanly "
                f"(exit code {run.get('exitCode')})",
            )

    lines = (await client.get_process_report("extract", extract)).get("lines") or []
    text = "\n".join(str(line) for line in lines)
    if not text.strip():
        report.block("initial_load_unverified", f"{extract} returned an empty report")
    elif _FAILURE_INDICATOR.search(text):
        report.block(
            "initial_load_report_failure", f"Report of {extract} contains a failure indicator"
        )
    elif _OPERATOR_STOP.search(text):
        report.block("initial_load_operator_stop", f"Report of {extract} shows an operator stop")

    # A restart while gathering this evidence invalidates all of it.
    after = await runtime()
    if any(after.get(k) != before.get(k) for k in ("status", "lastStarted", "processId")):
        report.block("initial_load_changed", f"{extract} changed during inspection; check again")


async def _check_replicat(client: Any, details: Dict[str, Any], report: _Report) -> None:
    source_file = _source_file(details)
    declared_initial = details.get("source") == "tables" or bool(
        _SPECIALRUN.search(_config_text(details))
    )

    names = [i.get("name") for i in collection_items(await client.list_extracts())]
    too_many = len(names) > MAX_INSPECTED_EXTRACTS
    if too_many:
        names = []
    fetched = await asyncio.gather(*(client.get_extract_status(n) for n in names))
    extracts = dict(zip(names, map(unwrap_response, fetched), strict=True))

    file_producers = [
        name
        for name, extract in extracts.items()
        if source_file and source_file in _parameter_files(_config_text(extract), "EXTFILE")
    ]

    if declared_initial or file_producers:
        if len(file_producers) != 1:
            found = "several Extracts" if file_producers else "no Extract"
            reason = f"{found} write the file this Initial Load Replicat reads"
            if too_many:
                reason = f"more than {MAX_INSPECTED_EXTRACTS} Extracts to inspect"
            report.block("initial_load_unverified", f"Cannot verify the load: {reason}")
            return
        await _check_initial_load_extract(client, file_producers[0], report)
        return

    if too_many:
        report.warn("too_many_extracts", "Skipped the check for a local CDC Extract")
    elif source_file is None:
        report.warn("source_unknown", "The Replicat's detail doesn't name its trail")
    else:
        producers = [
            (name, extract)
            for name, extract in extracts.items()
            if _reads_transaction_logs(extract)
            and source_file in _parameter_files(_config_text(extract), "EXTTRAIL")
        ]
        if not producers:
            report.warn(
                "no_local_producer",
                f"No local Extract writes {source_file}; expected if a distribution path "
                "delivers it",
            )
        for name, extract in producers:
            status = normalize_status(extract.get("status"))
            if status != "running":
                report.warn("producer_not_running", f"Extract {name} is {status}")


async def _warn_about_last_failure(
    client: Any, process_type: str, name: str, report: _Report
) -> None:
    try:
        body = await client.get_process_report(process_type, name)
    except GoldenGateAPIError:
        return
    errors = [m for m in parse_ogg_messages(body.get("lines") or []) if m["level"] == "ERROR"]
    if errors:
        last = errors[-1]
        report.warn("previous_abend", f"Last run failed with {last['code']}: {last['text']}")


async def check_start(client: Any, process_type: str, name: str) -> Dict[str, Any]:
    """Blockers and warnings for starting one process, from live GoldenGate state."""
    report = _Report(process_type, name)
    read = client.get_extract_status if process_type == "extract" else client.get_replicat_status

    try:
        details = unwrap_response(await read(name, use_cache=False))
        status = normalize_status(details.get("status"))
        report.data["status"] = status

        if status == "abended":
            await _warn_about_last_failure(client, process_type, name, report)
        if process_type == "replicat":
            await _check_replicat(client, details, report)
        elif not details.get("source"):
            report.warn("source_unknown", "The Extract's detail doesn't name its source")
    except ValueError as e:
        report.block("invalid_name", str(e))
    except GoldenGateAPIError as e:
        if e.http_status == 404:
            report.block("not_found", f"{process_type} {name} was not found")
        else:
            report.block("check_unavailable", redact_error_text(str(e)))

    return report.data


def blocked_names(reports: List[Dict[str, Any]]) -> List[str]:
    return [r["process"] for r in reports if r["blockers"]]
