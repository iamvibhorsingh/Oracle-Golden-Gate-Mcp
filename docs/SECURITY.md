# Security

## Key Security Features

### 1. Read-Only by Default
```bash
GG_READ_ONLY=true  # Prevents accidental changes
```
- Safe for AI assistant interactions
- Disable only when write operations are required

### 2. Secure Credentials
```bash
# Use environment variables, never hardcode
GG_DEPLOYMENT_1_PASSWORD=your_password
```
- Never commit .env file
- Use secrets manager in production
- Rotate credentials regularly
- **`Config.to_file(path, include_secrets=False)`** (default) writes placeholder passwords, not real secrets. Use `include_secrets=True` only in controlled exports.
- **`*_FILE` settings** (`GG_DEPLOYMENT_<N>_PASSWORD_FILE`, `GG_REST_API_TOKEN_FILE`, `GG_REST_API_KEYS_FILE`, `password_file` in JSON) read mounted secrets instead of environment variables. REST keys store only the SHA-256 of each token.
- **`backup_deployment_config`** returns JSON in the tool response only. **`save_config_snapshot`** writes a redacted snapshot to `GG_BACKUP_DIR` with owner-only permissions, and `compare_deployment_configs` refuses paths outside that directory and snapshots of other deployments.
- **Tool responses** are passed through `redact_sensitive_data` before serialization so nested keys like `password` / `token` / `api_key` and GoldenGate parameter-file credential lines (including the `config` and report `lines` arrays, and commented-out lines) are masked. **Audit logs** use the same helpers for action arguments (nested), result errors, and security-event details; database monitor connection errors are scrubbed for URL/basic-auth patterns.

### 3. SSL/TLS Verification
```bash
GG_DEPLOYMENT_1_VERIFY_SSL=true  # Always in production
```
- Disable only for testing with self-signed certs

### 4. Comprehensive Audit Logging
- All operations logged (configurable via `GG_AUDIT_LOG_PATH`, default `./logs/audit.log`)
- Includes timestamps, actions, results
- Review regularly for security monitoring
- **Hash-chained**: every entry carries `seq`, `prev_hash` and `hash` (SHA-256 of the entry, which includes the previous entry's hash). The chain continues across restarts and log rotation, so an edited, deleted, inserted or reordered entry is detectable:
  ```bash
  goldengate-audit-verify ./logs/audit.log
  ```
  It exits non-zero on a fault. Admins can also use the `verify_audit_log` and `search_audit_log` tools. This checks the log and its rotated files (`audit.log.1` ...) and reports the first bad entry as `<file>:<line>: <reason>`. Entries written before hash chaining was added are accepted at the start of the log; an unchained entry after the chain starts is reported.
- **Tamper-evident, not tamper-proof**: anyone who can rewrite the whole file can recompute the chain, and removing entries from either end leaves a valid, shorter chain. To catch that, periodically record the `head_hash` (and `last_seq`) from the verification somewhere the MCP host cannot write to (SIEM, another host), and pass it to later checks with `--contains <hash>`
- Run one server process per audit log file; two writers on the same file would fork the chain

### 5. Metrics Storage (Optional)
```bash
GG_ENABLE_METRICS=false  # Default: no SQLite store, no background collection
```
- When disabled (default): no local files written beyond the audit log, reduced attack surface
- When enabled (`GG_ENABLE_METRICS=true`): SQLite DB at `GG_METRICS_DB_PATH` — restrict file permissions (`chmod 600`) if the host is shared
- With metrics disabled, three tools are not registered (`diagnose_lag_issue`, `get_performance_baseline`, `get_lag_trend`) — acceptable trade-off for environments where local storage is a concern

### 6. Input Validation
- Process and report names must fully match an allowlist of characters before reaching a URL
- Required arguments are checked for every tool, over MCP and REST
- Unrecognised boolean settings, duplicate deployment names and unknown names in policy settings stop startup

### 7. Access Control
- **Roles**: `viewer`, `operator` and `admin` for REST callers defined in `GG_REST_API_KEYS`
- **Scoping**: a caller's `deployments` list applies to every tool, including fleet-wide ones
- **Write allowlist**: `GG_WRITE_DEPLOYMENTS` keeps other deployments read-only
- **Change tickets**: `GG_PROTECTED_DEPLOYMENTS` requires a `change_ticket` matching `GG_CHANGE_TICKET_PATTERN`. The format is checked and the value audited; the ticket itself is not verified against your ticketing system
- **Limits**: `GG_MAX_BATCH_SIZE` and `GG_DISABLED_TOOLS`
- Denials are written to the audit log as `security` events and counted in `ggmcp_policy_denials_total`
- Use the `get_server_posture` tool to review the effective settings
- `GG_REST_API_TOKEN` is one shared admin identity and stdio clients run as admin, so named keys are the only way to attribute REST calls to individuals

## Best Practices

### Production Deployment
1. **Always use read-only mode** unless write operations are required
2. **Enable SSL/TLS verification** for all deployments
3. **Use strong passwords** (16+ characters, mixed case, numbers, symbols)
4. **Restrict network access** to MCP server
5. **Monitor audit logs** for suspicious activity
6. **Rotate credentials** every 90 days

### Development/Testing
1. Use separate credentials from production
2. Test in isolated environment first
3. Review audit logs after testing
4. Never use production credentials in .env.example

## Reporting Security Issues

Please report security issues through GitHub or contact the maintainers directly.

## Compliance

This project has not been audited or certified against any standard. It provides controls that can support a compliance program:
- **Audit evidence** - Hash-chained log of who did what, when, under which change ticket
- **Access control** - Roles, deployment scoping, change-control gates
- **Encryption in transit** - TLS to GoldenGate when `VERIFY_SSL` is on. TLS on the REST port is left to a reverse proxy
- **Personal data** - The audit log stores caller names and GoldenGate usernames; apply your retention policy to it, including rotated files

## Security Checklist

Before deploying to production:

- [ ] Read-only mode enabled, or `GG_WRITE_DEPLOYMENTS` and `GG_PROTECTED_DEPLOYMENTS` set
- [ ] REST callers use named keys with the lowest workable role
- [ ] REST port behind TLS
- [ ] `get_server_posture` reports no unexpected findings
- [ ] `goldengate-audit-verify` scheduled, with `head_hash` pinned off-host
- [ ] SSL/TLS verification enabled
- [ ] Strong passwords configured
- [ ] Audit logging enabled and monitored
- [ ] Network access restricted
- [ ] Credentials stored securely (not in code)
- [ ] Regular security updates applied
- [ ] Incident response plan documented
