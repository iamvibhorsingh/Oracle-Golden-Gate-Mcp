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
- **`backup_deployment_config`** returns JSON in the tool response only; it does not write credentials under `./backups/`.
- **Tool responses** are passed through `redact_sensitive_data` before serialization so nested keys like `password` / `token` / `api_key` and obvious GoldenGate parameter-file credential lines are masked. **Audit logs** use the same helpers for action arguments (nested), result errors, and security-event details; database monitor connection errors are scrubbed for URL/basic-auth patterns.

### 3. SSL/TLS Verification
```bash
GG_DEPLOYMENT_1_VERIFY_SSL=true  # Always in production
```
- Disable only for testing with self-signed certs

### 4. Comprehensive Audit Logging
- All operations logged (configurable via `GG_AUDIT_LOG_PATH`, default `./logs/audit.log`)
- Includes timestamps, actions, results
- Review regularly for security monitoring

### 5. Metrics Storage (Optional)
```bash
GG_ENABLE_METRICS=false  # Disables SQLite store and background collection
```
- When disabled: no local files written beyond the audit log, reduced attack surface
- When enabled (default): SQLite DB at `GG_METRICS_DB_PATH` — restrict file permissions (`chmod 600`) if the host is shared
- Disabling metrics removes three tools from the server (`diagnose_lag_issue`, `get_performance_baseline`, `get_lag_trend`) — acceptable trade-off for environments where local storage is a concern

### 6. Input Validation
- Process names sanitized (alphanumeric + underscore only)
- Prevents injection attacks
- All inputs validated with Pydantic

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

This server supports:
- **SOC 2** - Comprehensive audit logging
- **GDPR** - No PII stored
- **HIPAA** - Encryption in transit, audit trails

## Security Checklist

Before deploying to production:

- [ ] Read-only mode enabled
- [ ] SSL/TLS verification enabled
- [ ] Strong passwords configured
- [ ] Audit logging enabled and monitored
- [ ] Network access restricted
- [ ] Credentials stored securely (not in code)
- [ ] Regular security updates applied
- [ ] Incident response plan documented
