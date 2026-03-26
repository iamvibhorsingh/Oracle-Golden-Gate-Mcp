"""
Setup script for local GoldenGate Free replication testing.
Creates credentials, Extract (EXT1), and Replicat (REP1) via the REST API.

Usage:
    python setup_local_replication.py
"""

import os
import time
import httpx
import subprocess
from dotenv import load_dotenv

load_dotenv()

GG_URL  = os.getenv("GG_DEPLOYMENT_1_URL", "https://localhost:9100")
GG_USER = os.getenv("GG_DEPLOYMENT_1_USERNAME", "oggadmin")
GG_PASS = os.getenv("GG_DEPLOYMENT_1_PASSWORD", "GGMCP_Admin123")
DB_USER = "ggadmin"
DB_PASS = os.getenv("ORACLE_PWD", "GGMCP_Admin123")
DB_CONN = "oracle-db:1521/FREEPDB1"   # PDB

client = httpx.Client(
    auth=(GG_USER, GG_PASS),
    verify=False,
    timeout=30.0,
    headers={"Accept": "application/json", "Content-Type": "application/json"},
)

def _ok(resp: httpx.Response, label: str) -> bool:
    if resp.status_code in (200, 201):
        print(f"   ✓ {label}")
        return True
    try:
        body = resp.json()
        msgs = body.get("messages", [])
        detail = msgs[0].get("title", "") if msgs else resp.text
    except Exception:
        detail = resp.text
    print(f"   ✗ {label}: [{resp.status_code}] {detail}")
    return False


def wait_for_api():
    """Wait for GoldenGate REST API to be ready."""
    print("Waiting for GoldenGate REST API...")
    for _ in range(60):
        try:
            resp = client.get(f"{GG_URL}/services/v2/deployments")
            if resp.status_code == 200:
                print("   ✓ API is ready")
                return True
        except httpx.RequestError:
            pass
        time.sleep(2)
    print("   ✗ API not ready")
    return False


def _create_credential(alias, userid, password):
    payload = {
        "userid": userid,
        "password": password,
    }
    resp = client.post(
        f"{GG_URL}/services/v2/credentials/OracleGoldenGate/{alias}",
        json=payload,
    )
    if resp.status_code == 409:
        resp = client.put(
            f"{GG_URL}/services/v2/credentials/OracleGoldenGate/{alias}",
            json=payload,
        )
    _ok(resp, f"Credential alias '{alias}' created")


def step_credentials():
    """1. Add database credential alias 'LocalDB'."""
    print("1. Adding database credentials...")
    _create_credential("LocalDB", f"{DB_USER}@{DB_CONN}", DB_PASS)


def step_checkpoint():
    """2. Delegate Checkpoint table creation to GoldenGate via AdminClient."""
    print("2. Creating Checkpoint Table (gg_tgt.gg_checkpoint)...")
    script = f"""
connect https://localhost:8443 as {GG_USER} password {GG_PASS} !
dblogin useridalias LocalDB domain OracleGoldenGate
add checkpointtable gg_tgt.gg_checkpoint
"""
    try:
        r = subprocess.run(
            ["docker", "exec", "-i", "ggmcp-goldengate", "adminclient"],
            input=script.encode("utf-8"),
            capture_output=True,
            check=False
        )
        if r.returncode != 0:
            print(f"   ✗ Checkpoint table creation failed (stderr): {r.stderr.decode('utf-8')}")
        else:
            print("   ✓ Checkpoint table ready")
    except Exception as e:
        print(f"   ✗ Error creating checkpoint table: {e}")


def step_extract():
    """3. Create an Extract process EXT1 that captures from transaction logs."""
    print("3. Creating Extract 'EXT1' (POST /extracts/EXT1)...")

    # Delete if exists (idempotent re-run)
    client.delete(f"{GG_URL}/services/v2/extracts/EXT1")

    payload = {
        "description": "Integration Test Extract (GG_SRC)",
        "source": "tranlogs",
        "begin": "now",
        "status": "stopped",
        "credentials": {
            "domain": "OracleGoldenGate",
            "alias": "LocalDB",
        },
        "targets": [
            {
                "name": "aa",
                "sizeMB": 500,
            }
        ],
        "config": [
            "EXTRACT EXT1",
            "USERIDALIAS LocalDB DOMAIN OracleGoldenGate",
            "EXTTRAIL aa",
            "TABLE GG_SRC.*;",
        ],
    }
    resp = client.post(f"{GG_URL}/services/v2/extracts/EXT1", json=payload)
    extract_created = _ok(resp, "Extract EXT1 created")

    # GG Free sometimes returns 500 even on success
    if not extract_created and resp.status_code == 500:
        try:
            body = resp.json()
            msgs = body.get("messages", [])
            if any("added" in m.get("title", "").lower() for m in msgs):
                print("   ⚠ Extract created (API returned 500 but confirmed added)")
                extract_created = True
        except Exception:
            pass

    if extract_created:
        time.sleep(2)
        check = client.get(f"{GG_URL}/services/v2/extracts/EXT1")
        if check.status_code != 200:
            print(f"   ✗ Extract EXT1 not found after creation (status {check.status_code}). Retrying...")
            resp = client.post(f"{GG_URL}/services/v2/extracts/EXT1", json=payload)
            _ok(resp, "Extract EXT1 created (retry)")
            time.sleep(2)

        print("   Registering EXT1 with Database (LogMiner)...")
        script = f"""
connect https://localhost:8443 as {GG_USER} password {GG_PASS} !
dblogin useridalias LocalDB domain OracleGoldenGate
register extract EXT1 database
"""
        try:
            r = subprocess.run(
                ["docker", "exec", "-i", "ggmcp-goldengate", "adminclient"],
                input=script.encode("utf-8"),
                capture_output=True,
                check=False
            )
            if r.returncode != 0:
                print(f"   ✗ Adminclient failed (stderr): {r.stderr.decode('utf-8')}")
                print(f"   ✗ Adminclient stdout: {r.stdout.decode('utf-8')}")
            else:
                out = r.stdout.decode('utf-8')
                if "ERROR" in out:
                    print(f"   ✗ Adminclient returned an error: {out}")
                else:
                    print("   ✓ EXT1 registered with database")
                    # print(out) # debug if needed
        except Exception as e:
            print(f"   ✗ Failed to register EXT1: {e}")

        print("   Starting EXT1...")
        start_resp = client.patch(
            f"{GG_URL}/services/v2/extracts/EXT1", 
            json={"status": "running"}
        )
        _ok(start_resp, "EXT1 start command executed")


def step_replicat():
    """4. Create a Replicat process REP1 that reads from trail aa."""
    print("4. Creating Replicat 'REP1' (POST /replicats/REP1)...")

    # Delete if exists
    client.delete(f"{GG_URL}/services/v2/replicats/REP1")
    
    payload = {
        "description": "Integration Test Replicat (GG_TGT)",
        "source": {
            "$schema": "ogg:trailRef",
            "name": "aa",
        },
        "begin": "now",
        "status": "stopped",
        "mode": {
            "type": "nonintegrated",
        },
        "credentials": {
            "domain": "OracleGoldenGate",
            "alias": "LocalDB",
        },
        "checkpoint": {
            "table": "GG_TGT.GG_CHECKPOINT",
        },
        "config": [
            "REPLICAT REP1",
            "USERIDALIAS LocalDB DOMAIN OracleGoldenGate",
            "MAP GG_SRC.*, TARGET GG_TGT.*;",
        ],
    }
    resp = client.post(f"{GG_URL}/services/v2/replicats/REP1", json=payload)
    if _ok(resp, "Replicat REP1 created"):
        print("   Starting REP1...")
        start_resp = client.patch(
            f"{GG_URL}/services/v2/replicats/REP1", 
            json={"status": "running"}
        )
        _ok(start_resp, "REP1 start command executed")


def step_verify():
    """4. Verify processes exist and are running."""
    print("\n5. Verifying status...")
    time.sleep(2) # Give them a second to start
    for kind in ("extracts", "replicats"):
        resp = client.get(f"{GG_URL}/services/v2/{kind}")
        if resp.status_code == 200:
            items = resp.json().get("response", {}).get("items", [])
            for item in items:
                print(f"   {kind[:-1].capitalize()} {item.get('name', '?')} status: {item.get('status', 'unknown')}")
        else:
            print(f"   ✗ Could not list {kind}")


def main():
    print(f"--- GoldenGate Integration Test Setup ({GG_URL}) ---\n")
    if not wait_for_api():
        return
    step_credentials()
    step_checkpoint()
    step_extract()
    step_replicat()
    step_verify()
    print("\n--- Done! Run `python test_replication.py` to verify data sync ---")


if __name__ == "__main__":
    main()
