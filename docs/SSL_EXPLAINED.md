# How SSL/TLS Works in This MCP Server

## Architecture Overview

```
┌─────────────┐         ┌──────────────┐         ┌─────────────────┐
│   Claude    │  stdio  │  MCP Server  │  HTTPS  │   GoldenGate    │
│  (AI Tool)  │◄───────►│   (Python)   │◄───────►│   REST API      │
└─────────────┘         └──────────────┘         └─────────────────┘
                              ▲
                              │ SSL/TLS
                              │ Verification
                              ▼
                        ┌──────────────┐
                        │  httpx lib   │
                        │  (HTTP/2)    │
                        └──────────────┘
```

## SSL/TLS Configuration

### 1. Client-Side SSL (MCP Server → GoldenGate)

The MCP server acts as an **HTTPS client** connecting to GoldenGate's REST API:

```python
# In goldengate_client.py
self.client = httpx.AsyncClient(
    auth=(username, password),
    verify=verify_ssl,          # ← SSL verification here
    timeout=timeout,
    follow_redirects=True,
    headers={
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
)
```

### 2. SSL Verification Modes

#### Production Mode (verify_ssl=True)
```bash
# .env file
GG_DEPLOYMENT_1_VERIFY_SSL=true
```

**What happens:**
1. MCP server connects to GoldenGate HTTPS endpoint
2. GoldenGate presents its SSL certificate
3. httpx validates:
   - Certificate is signed by trusted CA
   - Certificate is not expired
   - Hostname matches certificate CN/SAN
   - Certificate chain is valid
4. If validation fails → Connection refused
5. If validation succeeds → Encrypted connection established

**Certificate Requirements:**
- Must be signed by a trusted Certificate Authority (CA)
- Or CA certificate must be in system trust store
- Or provide custom CA bundle (see below)

#### Development/Testing Mode (verify_ssl=False)
```bash
# .env file
GG_DEPLOYMENT_1_VERIFY_SSL=false
```

**What happens:**
1. MCP server connects to GoldenGate HTTPS endpoint
2. GoldenGate presents its SSL certificate
3. httpx **skips all validation**
4. Connection established regardless of certificate validity

**⚠️ Security Risk:**
- Vulnerable to man-in-the-middle attacks
- Should NEVER be used in production
- Only for testing with self-signed certificates

### 3. Custom CA Certificates

If GoldenGate uses certificates from internal CA:

```python
# Option 1: System-wide (recommended)
# Add CA cert to system trust store
# Linux: /etc/ssl/certs/
# Windows: certmgr.msc
# macOS: Keychain Access

# Option 2: Per-connection (advanced)
self.client = httpx.AsyncClient(
    verify="/path/to/ca-bundle.crt"  # Custom CA bundle
)
```

## Communication Flow

### Step-by-Step SSL Handshake

```
1. MCP Server → GoldenGate: ClientHello
   - Supported TLS versions (1.2, 1.3)
   - Supported cipher suites
   
2. GoldenGate → MCP Server: ServerHello
   - Selected TLS version
   - Selected cipher suite
   - Server certificate
   
3. MCP Server validates certificate:
   ✓ Signed by trusted CA?
   ✓ Not expired?
   ✓ Hostname matches?
   ✓ Chain valid?
   
4. If valid:
   - Generate session keys
   - Establish encrypted channel
   - Send HTTP requests over TLS
   
5. If invalid:
   - Raise SSLError
   - Connection refused
```

### Example Request Flow

```python
# User asks Claude: "Check GoldenGate lag"
# 
# 1. Claude calls MCP tool: get_extract_lag(deployment="prod", extract_name="SALES")
# 
# 2. MCP Server:
async def _get_extract_lag(self, deployment, extract_name):
    client = self._get_client(deployment)  # Gets httpx client
    return await client.get_extract_lag(extract_name)

# 3. httpx makes HTTPS request:
#    GET https://gg-host:9100/services/v2/extracts/SALES/lag
#    - TLS handshake (if verify_ssl=true, validates cert)
#    - Basic auth header: Authorization: Basic base64(user:pass)
#    - Encrypted over TLS 1.2/1.3
#
# 4. GoldenGate responds:
#    {"lag": "00:00:05", "status": "running"}
#    - Encrypted response
#
# 5. httpx decrypts and returns to MCP server
# 6. MCP server returns to Claude
# 7. Claude presents to user: "Lag is 5 seconds"
```

## Security Considerations

### What's Encrypted
✅ **All HTTP traffic** between MCP server and GoldenGate
✅ **Credentials** (username/password in Basic Auth header)
✅ **Request/response bodies** (JSON data)
✅ **Headers** (including authentication)

### What's NOT Encrypted
❌ **stdio communication** between Claude and MCP server (local process)
❌ **Environment variables** in .env file (file system security)
❌ **Audit logs** (stored as plaintext JSON)

### Best Practices

1. **Always use verify_ssl=true in production**
   ```bash
   GG_DEPLOYMENT_1_VERIFY_SSL=true
   ```

2. **Use proper certificates**
   - Get certificates from trusted CA
   - Or use internal CA and distribute CA cert
   - Never use self-signed certs in production

3. **Protect credentials**
   ```bash
   # .env file permissions
   chmod 600 .env  # Owner read/write only
   ```

4. **Use strong TLS versions**
   - httpx defaults to TLS 1.2+ (good)
   - Disable TLS 1.0/1.1 on GoldenGate side

5. **Monitor certificate expiration**
   - Certificates typically expire after 1 year
   - Set up alerts 30 days before expiration

## Troubleshooting SSL Issues

### Error: "SSL: CERTIFICATE_VERIFY_FAILED"

**Cause:** Certificate validation failed

**Solutions:**
```bash
# 1. Check certificate is valid
openssl s_client -connect gg-host:9100 -showcerts

# 2. Check hostname matches certificate
# Certificate CN/SAN must match "gg-host"

# 3. Add CA to trust store (if internal CA)
# Linux:
sudo cp ca-cert.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates

# 4. Temporary workaround (testing only)
GG_DEPLOYMENT_1_VERIFY_SSL=false
```

### Error: "SSL: WRONG_VERSION_NUMBER"

**Cause:** GoldenGate not using HTTPS

**Solution:**
```bash
# Check URL uses https://
GG_DEPLOYMENT_1_URL=https://gg-host:9100  # Not http://
```

### Error: "Connection refused"

**Cause:** GoldenGate REST API not accessible

**Solutions:**
```bash
# 1. Check GoldenGate is running
# 2. Check firewall allows port 9100
# 3. Check URL is correct
# 4. Test with curl:
curl -k -u user:pass https://gg-host:9100/services/v2/deployments
```

## Advanced: Custom SSL Configuration

For advanced use cases, you can customize SSL settings:

```python
# In goldengate_client.py (advanced users)
import ssl

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = True
ssl_context.verify_mode = ssl.CERT_REQUIRED
ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

self.client = httpx.AsyncClient(
    verify=ssl_context,  # Custom SSL context
    # ... other settings
)
```

## Summary

**SSL in this MCP server:**
- ✅ Encrypts all traffic to GoldenGate REST API
- ✅ Validates certificates by default (production-safe)
- ✅ Supports custom CA certificates
- ✅ Uses modern TLS 1.2/1.3
- ✅ Can be disabled for testing (not recommended)

**The MCP server itself does NOT:**
- ❌ Expose any HTTPS endpoints (it's a client, not a server)
- ❌ Need its own SSL certificate
- ❌ Handle incoming HTTPS connections

**Communication paths:**
1. **Claude ↔ MCP Server**: stdio (local, no network)
2. **MCP Server ↔ GoldenGate**: HTTPS with SSL/TLS (encrypted)
