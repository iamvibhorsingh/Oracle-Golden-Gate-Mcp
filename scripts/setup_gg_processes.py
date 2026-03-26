#!/usr/bin/env python3
"""
setup_gg_processes.py — Configure 10 Extracts + 10 Replicats on GoldenGate Free
using AdminClient via docker exec (REST API is read-only for process management).

Usage:
    python scripts/setup_gg_processes.py [--password GGMCP_Admin123]
                                         [--container ggmcp-goldengate]
                                         [--deployment LocalTest]

Then verify via REST API or run the stress tests.
"""

import argparse
import subprocess
import sys
import time

import httpx
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TABLES = [
    "customers", "orders", "order_items", "products", "inventory_events",
    "payments", "audit_log", "user_sessions", "notifications", "metrics_raw",
]

DB_CONNECT = "oracle-db:1521/FREEPDB1"
DB_USER = "ggadmin"


def run_admin_client(container: str, commands: list[str], timeout: int = 120):
    """Run commands in GG AdminClient via docker exec, return stdout."""
    script = "\n".join(commands) + "\nEXIT\n"
    print(f"\n  Sending {len(commands)} commands to AdminClient...")
    result = subprocess.run(
        ["docker", "exec", "-i", container, "/u01/ogg/bin/adminclient"],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = result.stdout + result.stderr
    return output


def wait_for_gg(url: str, user: str, password: str, timeout: int = 300):
    """Wait for GG REST API to be reachable."""
    print("Waiting for GoldenGate REST API...", end="", flush=True)
    client = httpx.Client(base_url=url, auth=(user, password),
                          verify=False, timeout=10.0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = client.get("/services/v2/deployments")
            if r.is_success:
                print(" ready.")
                client.close()
                return True
        except httpx.HTTPError:
            pass
        print(".", end="", flush=True)
        time.sleep(5)
    print(" TIMEOUT")
    client.close()
    return False


def setup_credentials(container: str, deployment: str, user: str,
                      password: str, db_password: str):
    """Create credential store and add aliases."""
    print("\n=== Setting up credentials ===")
    commands = [
        f"CONNECT https://localhost:8443 DEPLOYMENT {deployment} "
        f"AS {user} PASSWORD {password} !",
        "",
        "ADD CREDENTIALSTORE",
        f"ALTER CREDENTIALSTORE ADD USER {DB_USER}@{DB_CONNECT} "
        f"PASSWORD {db_password} ALIAS ggadmin_src DOMAIN OracleGoldenGate",
        f"ALTER CREDENTIALSTORE ADD USER {DB_USER}@{DB_CONNECT} "
        f"PASSWORD {db_password} ALIAS ggadmin_tgt DOMAIN OracleGoldenGate",
        "",
        "INFO CREDENTIALSTORE",
    ]
    output = run_admin_client(container, commands)
    print(output)
    return "successfully" in output.lower() or "already" in output.lower()


def setup_extracts(container: str, deployment: str, user: str,
                   password: str):
    """Create 10 Extract processes with trail files."""
    print("\n=== Creating 10 Extract processes ===")

    commands = [
        f"CONNECT https://localhost:8443 DEPLOYMENT {deployment} "
        f"AS {user} PASSWORD {password} !",
        "",
        "DBLOGIN USERIDALIAS ggadmin_src DOMAIN OracleGoldenGate",
        "",
    ]

    for i, tbl in enumerate(TABLES):
        name = f"EXT{i + 1:02d}"
        trail = f"{chr(97 + i)}a"  # aa, ba, ca, ... ja

        commands.extend([
            f"ADD EXTRACT {name}, TRANLOG, BEGIN NOW",
            f"ADD EXTTRAIL {trail}, EXTRACT {name}",
            f"REGISTER EXTRACT {name}, DATABASE",
            "",
        ])

    commands.append("INFO ALL")
    output = run_admin_client(container, commands, timeout=180)
    print(output)
    return output


def write_extract_params(container: str):
    """Write parameter files for each Extract process."""
    print("\n=== Writing Extract parameter files ===")
    for i, tbl in enumerate(TABLES):
        name = f"EXT{i + 1:02d}"
        trail = f"{chr(97 + i)}a"
        params = (
            f"EXTRACT {name}\n"
            f"USERIDALIAS ggadmin_src DOMAIN OracleGoldenGate\n"
            f"EXTTRAIL {trail}\n"
            f"TABLE FREEPDB1.GG_SRC.{tbl.upper()};\n"
        )
        # Write param file into container
        result = subprocess.run(
            ["docker", "exec", "-i", container, "bash", "-c",
             f"cat > /u02/Deployment/etc/conf/ogg/{name}.prm"],
            input=params,
            capture_output=True,
            text=True,
        )
        status = "OK" if result.returncode == 0 else "FAILED"
        print(f"  {name}.prm ({tbl}): {status}")


def setup_replicats(container: str, deployment: str, user: str,
                    password: str):
    """Create 10 Replicat processes."""
    print("\n=== Creating 10 Replicat processes ===")

    commands = [
        f"CONNECT https://localhost:8443 DEPLOYMENT {deployment} "
        f"AS {user} PASSWORD {password} !",
        "",
        "DBLOGIN USERIDALIAS ggadmin_tgt DOMAIN OracleGoldenGate",
        "",
        "ADD CHECKPOINTTABLE ggadmin_tgt.chkptab",
        "",
    ]

    for i, tbl in enumerate(TABLES):
        name = f"REP{i + 1:02d}"
        trail = f"{chr(97 + i)}a"

        commands.extend([
            f"ADD REPLICAT {name}, EXTTRAIL {trail}, "
            f"CHECKPOINTTABLE ggadmin_tgt.chkptab",
            "",
        ])

    commands.append("INFO ALL")
    output = run_admin_client(container, commands, timeout=180)
    print(output)
    return output


def write_replicat_params(container: str):
    """Write parameter files for each Replicat process."""
    print("\n=== Writing Replicat parameter files ===")
    for i, tbl in enumerate(TABLES):
        name = f"REP{i + 1:02d}"
        params = (
            f"REPLICAT {name}\n"
            f"USERIDALIAS ggadmin_tgt DOMAIN OracleGoldenGate\n"
            f"MAP FREEPDB1.GG_SRC.{tbl.upper()}, "
            f"TARGET FREEPDB1.GG_TGT.{tbl.upper()};\n"
        )
        result = subprocess.run(
            ["docker", "exec", "-i", container, "bash", "-c",
             f"cat > /u02/Deployment/etc/conf/ogg/{name}.prm"],
            input=params,
            capture_output=True,
            text=True,
        )
        status = "OK" if result.returncode == 0 else "FAILED"
        print(f"  {name}.prm ({tbl}): {status}")


def start_processes(container: str, deployment: str, user: str,
                    password: str):
    """Start all Extract and Replicat processes."""
    print("\n=== Starting all processes ===")

    commands = [
        f"CONNECT https://localhost:8443 DEPLOYMENT {deployment} "
        f"AS {user} PASSWORD {password} !",
        "",
    ]

    for i in range(1, 11):
        commands.append(f"START EXTRACT EXT{i:02d}")

    # Small delay between starting extracts and replicats
    for i in range(1, 11):
        commands.append(f"START REPLICAT REP{i:02d}")

    commands.extend(["", "INFO ALL"])
    output = run_admin_client(container, commands, timeout=180)
    print(output)
    return output


def verify_via_rest(url: str, user: str, password: str):
    """Verify processes via REST API (what the MCP server will use)."""
    print("\n=== Verifying via REST API (MCP perspective) ===")
    client = httpx.Client(base_url=url, auth=(user, password),
                          verify=False, timeout=30.0)
    try:
        for kind in ("extracts", "replicats"):
            r = client.get(f"/services/v2/{kind}")
            if r.is_success:
                body = r.json()
                items = body.get("response", {}).get("items", [])
                print(f"  {kind}: {len(items)} found")
                for item in items:
                    name = item.get("name", item.get("processName", "?"))
                    status = item.get("status", "?")
                    print(f"    {name}: {status}")
            else:
                print(f"  {kind}: {r.status_code}")
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Set up GoldenGate stress-test processes via AdminClient")
    parser.add_argument("--url", default="https://localhost:9100",
                        help="GG REST API URL (for verification)")
    parser.add_argument("--user", default="oggadmin")
    parser.add_argument("--password", default="GGMCP_Admin123")
    parser.add_argument("--db-password", default="GGMCP_Admin123",
                        help="Database password for ggadmin user")
    parser.add_argument("--deployment", default="LocalTest")
    parser.add_argument("--container", default="ggmcp-goldengate",
                        help="Docker container name for GoldenGate")
    parser.add_argument("--skip-start", action="store_true",
                        help="Create processes but don't start them")
    args = parser.parse_args()

    print("GoldenGate Stress-Test Setup (via AdminClient)")
    print(f"  Container:  {args.container}")
    print(f"  Deployment: {args.deployment}")
    print(f"  REST URL:   {args.url}")

    if not wait_for_gg(args.url, args.user, args.password):
        print("GoldenGate not reachable. Is Docker running?")
        sys.exit(1)

    # Step 1: Credentials
    setup_credentials(args.container, args.deployment,
                      args.user, args.password, args.db_password)

    # Step 2: Write parameter files first
    write_extract_params(args.container)
    write_replicat_params(args.container)

    # Step 3: Create Extract processes
    setup_extracts(args.container, args.deployment,
                   args.user, args.password)

    # Step 4: Create Replicat processes
    setup_replicats(args.container, args.deployment,
                    args.user, args.password)

    # Step 5: Start
    if not args.skip_start:
        start_processes(args.container, args.deployment,
                        args.user, args.password)

    # Step 6: Verify via REST
    verify_via_rest(args.url, args.user, args.password)

    print("\nDone. Next steps:")
    print("  python scripts/generate_load.py --duration 60  # optional DML load")
    print("  pytest tests/integration/test_stress_mcp.py -v --tb=short")


if __name__ == "__main__":
    main()
