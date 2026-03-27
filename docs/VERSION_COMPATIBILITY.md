# GoldenGate Version Compatibility

## Supported versions

| GG Version | Architecture | REST API | This MCP |
|------------|-------------|----------|----------|
| 23c | Microservices | v2 (full) | ✅ Full support |
| 21c | Microservices | v2 | ✅ Full support (primary target) |
| 19c | Microservices | v2 (partial) | ⚠️ Partial — see below |
| 12.3 | Microservices | v1 only | ⚠️ Limited — see below |
| 12.2 and older | Classic (GGSCI only) | ❌ None | ❌ Not supported as-is |

---

## GG 19c (Microservices)

GG 19c ships with the Microservices Architecture and exposes a REST API, but with limitations:

**What works:**
- `list_deployments`, `list_extracts`, `list_replicats`
- `get_extract_status`, `get_replicat_status`
- `start_extract`, `stop_extract`, `start_replicat`, `stop_replicat`
- `get_process_config`, `get_process_statistics` (basic)

**What may not work or may return null:**
- Lag fields (`lag_at_chkpt`, `time_since_chkpt`) — less reliably populated in 19c REST responses
- Performance metrics service (`pmsrvr`) — available from 21c onwards
- Some `adminsrvr/v2/` sub-paths may differ; the MCP may fall back to `_source: "detail_fallback"` more often

**To try 19c:** No code changes needed. Configure the deployment URL as usual and expect some fields to return `null`. The MCP handles null lag gracefully and will just report `severity: normal` with no baseline data.

---

## GG 12.3 (Microservices, v1 API only)

GG 12.3 was the first Microservices release. The REST API exists but uses `/v1/` paths rather than `/v2/`.

**What would need to change:**
- The `GoldenGateClient` in `client.py` hardcodes `/v2/` paths. You'd need to either:
  - Add a `api_version: str = "v2"` field to `DeploymentConfig` and substitute it in all URL construction
  - Or add a 12.3-specific client subclass with v1 path mappings

---

## GG 12.2 and older (Classic Architecture)

Pre-12.3 GoldenGate uses the **Classic Architecture** — there is no REST API. Management is done through GGSCI (command-line) or the older Java-based Management Pack.

**Options for Classic GG support:**

### Option A: GGSCI wrapper - Hard to get right, not recommended
Wrap `ggsci` commands via `subprocess` (similar to how `setup_gg_processes.py` calls `adminclient`). You could implement a subset of tools — `list_extracts`, `get_extract_status`, `start_extract` — by parsing GGSCI output. Lag would come from `INFO EXTRACT <name> DETAIL` output parsing.

Downsides: brittle (text parsing), no structured JSON, requires SSH or local access to the GG host.

### Option B: GG Manager REST shim - Much harder, not recommended
GG Classic has a Manager process that accepts TCP connections. There are community projects that wrap this in a REST-like interface, but nothing official.

### Option C: Upgrade to Microservices
Just upgrade to Microservices. GG 19c+ Microservices can coexist with or replace Classic installations and unlocks the full REST API this MCP targets. Make your lives easier.

---

## Summary recommendation

| Scenario | Recommendation |
|----------|---------------|
| GG 21c or 23c | Use as-is |
| GG 19c Microservices | Use as-is, expect some null lag fields |
| GG 12.3 Microservices | Small code change to support v1 API paths |
| GG 12.2 Classic or older | GGSCI wrapper or upgrade to Microservices |
