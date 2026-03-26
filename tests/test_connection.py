"""
Example test script for GoldenGate MCP Server

This script demonstrates how to test the GoldenGate client directly
without the full MCP server infrastructure.
"""

import asyncio
import os

from goldengate_mcp_server.config import Config
from goldengate_mcp_server.goldengate_client import GoldenGateClient


async def test_goldengate_connection():
    """Test basic connectivity to GoldenGate deployment."""

    print("=" * 70)
    print("GoldenGate MCP Server - Connection Test")
    print("=" * 70)
    print()

    # Load configuration
    try:
        config = Config.from_env()
        print("✓ Configuration loaded successfully")
        print(f"  - Read-only mode: {config.read_only}")
        print(f"  - Deployments configured: {len(config.deployments)}")
        print()
    except Exception as e:
        print(f"✗ Failed to load configuration: {e}")
        print("\nPlease ensure environment variables are set correctly.")
        print("See .env.example for required variables.")
        return

    # Test each deployment
    for deployment in config.deployments:
        print(f"Testing deployment: {deployment.name}")
        print(f"  URL: {deployment.base_url}")
        print(f"  Username: {deployment.username}")
        print(f"  SSL Verification: {deployment.verify_ssl}")
        print()

        try:
            # Create client
            client = GoldenGateClient(
                base_url=deployment.base_url,
                username=deployment.username,
                password=deployment.password,
                verify_ssl=deployment.verify_ssl,
                timeout=config.request_timeout
            )

            # Test connection - get deployment info
            print("  Testing API connection...")
            info = await client.get_deployment_info()
            print("  ✓ Connected successfully")
            print(f"    Version: {info.get('version', 'N/A')}")
            print()

            # List extracts
            print("  Fetching Extract processes...")
            extracts = await client.list_extracts()
            extract_list = extracts.get('items', [])
            print(f"  ✓ Found {len(extract_list)} Extract(s)")
            for ext in extract_list[:3]:  # Show first 3
                print(f"    - {ext.get('name')}: {ext.get('status')}")
            if len(extract_list) > 3:
                print(f"    ... and {len(extract_list) - 3} more")
            print()

            # List replicats
            print("  Fetching Replicat processes...")
            replicats = await client.list_replicats()
            replicat_list = replicats.get('items', [])
            print(f"  ✓ Found {len(replicat_list)} Replicat(s)")
            for rep in replicat_list[:3]:  # Show first 3
                print(f"    - {rep.get('name')}: {rep.get('status')}")
            if len(replicat_list) > 3:
                print(f"    ... and {len(replicat_list) - 3} more")
            print()

            # Test lag monitoring (if processes exist)
            if extract_list:
                extract_name = extract_list[0]['name']
                print(f"  Checking lag for Extract: {extract_name}")
                try:
                    lag = await client.get_extract_lag(extract_name)
                    print(f"  ✓ Lag: {lag.get('lag', 'N/A')}")
                    print(f"    Status: {lag.get('status')}")
                except Exception as e:
                    print(f"  ⚠ Could not get lag: {e}")
                print()

            print(f"✓ All tests passed for {deployment.name}")

        except Exception as e:
            print(f"✗ Error testing {deployment.name}: {e}")
            print()
            continue

        finally:
            await client.client.aclose()

        print("-" * 70)
        print()

    print("=" * 70)
    print("Test complete!")
    print("=" * 70)


async def test_health_check():
    """Test the health check functionality."""

    print("\n" + "=" * 70)
    print("Health Check Test")
    print("=" * 70)
    print()

    try:
        config = Config.from_env()

        for deployment in config.deployments:
            print(f"Health check for: {deployment.name}")

            client = GoldenGateClient(
                base_url=deployment.base_url,
                username=deployment.username,
                password=deployment.password,
                verify_ssl=deployment.verify_ssl,
                timeout=config.request_timeout
            )

            try:
                # Get all extracts and their status
                extracts = await client.list_extracts()
                extract_items = extracts.get('items', [])

                running = sum(1 for e in extract_items if e.get('status') == 'running')
                stopped = sum(1 for e in extract_items if e.get('status') == 'stopped')
                other = len(extract_items) - running - stopped

                print(f"  Extracts: {len(extract_items)} total")
                print(f"    Running: {running}")
                print(f"    Stopped: {stopped}")
                if other > 0:
                    print(f"    Other: {other} (may need attention)")

                # Get all replicats and their status
                replicats = await client.list_replicats()
                replicat_items = replicats.get('items', [])

                running = sum(1 for r in replicat_items if r.get('status') == 'running')
                stopped = sum(1 for r in replicat_items if r.get('status') == 'stopped')
                other = len(replicat_items) - running - stopped

                print(f"  Replicats: {len(replicat_items)} total")
                print(f"    Running: {running}")
                print(f"    Stopped: {stopped}")
                if other > 0:
                    print(f"    Other: {other} (may need attention)")

                overall = "HEALTHY" if other == 0 else "NEEDS ATTENTION"
                print(f"  Overall Status: {overall}")
                print()

            finally:
                await client.client.aclose()

    except Exception as e:
        print(f"✗ Health check failed: {e}")


if __name__ == "__main__":
    print("\nGoldenGate MCP Server Test Suite")
    print("Make sure environment variables are configured correctly\n")

    # Check for required environment variables
    required_vars = [
        "GG_DEPLOYMENT_1_NAME",
        "GG_DEPLOYMENT_1_URL",
        "GG_DEPLOYMENT_1_USERNAME",
        "GG_DEPLOYMENT_1_PASSWORD"
    ]

    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        print("⚠ Missing required environment variables:")
        for var in missing:
            print(f"  - {var}")
        print("\nPlease set these variables or copy .env.example to .env and configure.")
        exit(1)

    # Run tests
    asyncio.run(test_goldengate_connection())
    asyncio.run(test_health_check())
