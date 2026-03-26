"""
Integration test for GoldenGate local replication.
Inserts data into GG_SRC and verifies it replicates to GG_TGT.
Also checks Extract/Replicat status via the GoldenGate REST API.

Prerequisites:
    pip install oracledb httpx python-dotenv
    python setup_local_replication.py   (must succeed first)
"""

import time
import os

import oracledb
import httpx
from dotenv import load_dotenv

load_dotenv()

DB_PASS = os.getenv("ORACLE_PWD", "GGMCP_Admin123")
DB_CONN = "localhost:1521/FREEPDB1"

GG_URL  = os.getenv("GG_DEPLOYMENT_1_URL", "https://localhost:9100")
GG_USER = os.getenv("GG_DEPLOYMENT_1_USERNAME", "oggadmin")
GG_PASS = os.getenv("GG_DEPLOYMENT_1_PASSWORD", "GGMCP_Admin123")


def wait_for_replication(seconds=15):
    print(f"   Waiting {seconds}s for replication...")
    time.sleep(seconds)


def check_gg_status():
    """Check Extract and Replicat status via GoldenGate REST API."""
    print("\n1. Checking GoldenGate process status...")
    try:
        with httpx.Client(auth=(GG_USER, GG_PASS), verify=False, timeout=10) as client:
            for kind, name in [("extracts", "EXT1"), ("replicats", "REP1")]:
                resp = client.get(f"{GG_URL}/services/v2/{kind}/{name}")
                if resp.status_code == 200:
                    status = resp.json().get("response", {}).get("status", "unknown")
                    print(f"   {name}: {status}")
                    if status not in ("running",):
                        print(f"   ⚠ {name} is {status}. Attempting to start...")
                        patch_resp = client.patch(
                            f"{GG_URL}/services/v2/{kind}/{name}", 
                            json={"status": "running"}
                        )
                        if patch_resp.status_code == 200:
                            print(f"   ✓ {name} start command sent.")
                        else:
                            print(f"   ✗ Failed to start {name}: {patch_resp.status_code}")
                else:
                    print(f"   ✗ Could not get {name}: HTTP {resp.status_code}")
    except Exception as e:
        print(f"   ✗ Error connecting to GoldenGate API: {e}")


def insert_test_data():
    """Insert a test row into GG_SRC tables."""
    print("\n2. Inserting test data into GG_SRC...")
    try:
        with oracledb.connect(user="gg_src", password=DB_PASS, dsn=DB_CONN) as conn:
            cur = conn.cursor()

            # Use a unique email to avoid duplicate key errors on re-runs
            ts = int(time.time())
            email = f"test_{ts}@example.com"

            cur.execute(
                "INSERT INTO customers (name, email) VALUES (:1, :2)",
                (f"TestUser_{ts}", email),
            )
            # Get the generated ID
            cur.execute("SELECT id FROM customers WHERE email = :1", (email,))
            cust_id = cur.fetchone()[0]

            cur.execute(
                "INSERT INTO orders (customer_id, amount, status) VALUES (:1, :2, :3)",
                (cust_id, 42.00, "TEST"),
            )
            conn.commit()
            print(f"   ✓ Inserted customer {cust_id} ({email}) + order")
            return email, cust_id
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return None, None


def verify_replication(email, cust_id):
    """Check that the inserted row appeared in GG_TGT."""
    print("\n3. Verifying data in GG_TGT...")
    try:
        with oracledb.connect(user="gg_tgt", password=DB_PASS, dsn=DB_CONN) as conn:
            cur = conn.cursor()

            cur.execute("SELECT id, name, email FROM customers WHERE email = :1", (email,))
            row = cur.fetchone()
            if row:
                print(f"   ✓ Customer replicated: id={row[0]}, name={row[1]}")
            else:
                print("   ✗ Customer NOT found in target — replication may not be working")

            cur.execute(
                "SELECT order_id, amount, status FROM orders WHERE customer_id = :1 AND status = 'TEST'",
                (cust_id,),
            )
            row = cur.fetchone()
            if row:
                print(f"   ✓ Order replicated: order_id={row[0]}, amount={row[1]}")
            else:
                print("   ✗ Order NOT found in target — replication may not be working")
    except Exception as e:
        print(f"   ✗ Error: {e}")


def main():
    print("=" * 50)
    print("GoldenGate Replication Integration Test")
    print("=" * 50)

    check_gg_status()
    email, cust_id = insert_test_data()
    if not email:
        return
    wait_for_replication(15)
    verify_replication(email, cust_id)

    print("\n" + "=" * 50)
    print("Test complete.")
    print("=" * 50)


if __name__ == "__main__":
    main()
