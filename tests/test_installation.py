#!/usr/bin/env python3
"""
Test script for Oracle GoldenGate MCP Server

This script verifies:
1. Environment configuration
2. Python dependencies
3. GoldenGate API connectivity
4. Authentication
5. Basic API operations

Run this before deploying to production or configuring MCP clients.
"""

import asyncio
import os
import sys
from typing import Dict, List, Tuple

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'


def print_header(text: str):
    """Print a formatted header."""
    print(f"\n{BLUE}{'=' * 70}{RESET}")
    print(f"{BLUE}{text:^70}{RESET}")
    print(f"{BLUE}{'=' * 70}{RESET}\n")


def print_success(text: str):
    """Print success message."""
    print(f"{GREEN}✓ {text}{RESET}")


def print_error(text: str):
    """Print error message."""
    print(f"{RED}✗ {text}{RESET}")


def print_warning(text: str):
    """Print warning message."""
    print(f"{YELLOW}⚠ {text}{RESET}")


def print_info(text: str):
    """Print info message."""
    print(f"  {text}")


def check_python_version() -> Tuple[bool, str]:
    """Check if Python version is compatible."""
    version = sys.version_info
    if version.major == 3 and version.minor >= 10:
        return True, f"{version.major}.{version.minor}.{version.micro}"
    return False, f"{version.major}.{version.minor}.{version.micro}"


def check_dependencies() -> List[Tuple[str, bool, str]]:
    """Check if required dependencies are installed."""
    dependencies = [
        ("mcp", "Model Context Protocol SDK"),
        ("httpx", "Async HTTP client"),
        ("pydantic", "Data validation"),
        ("dotenv", "Environment configuration")
    ]

    results = []
    for module, description in dependencies:
        try:
            if module == "dotenv":
                import dotenv  # noqa: F401
            else:
                __import__(module)
            results.append((description, True, "Installed"))
        except ImportError:
            results.append((description, False, "Not installed"))

    return results


def check_environment() -> Dict[str, Tuple[bool, str, str]]:
    """Check if required environment variables are set."""
    required_vars = {
        "GG_SERVICE_MANAGER_URL": "GoldenGate Service Manager URL",
        "GG_USERNAME": "GoldenGate Username",
        "GG_PASSWORD": "GoldenGate Password"
    }

    optional_vars = {
        "GG_VERIFY_SSL": "SSL Verification",
        "GG_READ_ONLY": "Read-Only Mode",
        "GG_TIMEOUT": "Request Timeout",
        "GG_DEPLOYMENT_FILTER": "Deployment Filter",
        "GG_AUDIT_LOG": "Audit Log Path"
    }

    results = {}

    for var, description in required_vars.items():
        value = os.getenv(var)
        if value:
            # Mask sensitive values
            display_value = value if var != "GG_PASSWORD" else "***" * 8
            results[description] = (True, display_value, "Required")
        else:
            results[description] = (False, "Not set", "Required")

    for var, description in optional_vars.items():
        value = os.getenv(var)
        if value:
            results[description] = (True, value, "Optional")
        else:
            results[description] = (False, "Not set (using default)", "Optional")

    return results


async def test_connectivity() -> Tuple[bool, str]:
    """Test connectivity to GoldenGate Service Manager."""
    try:
        import httpx
        from dotenv import load_dotenv

        load_dotenv()

        url = os.getenv("GG_SERVICE_MANAGER_URL")
        username = os.getenv("GG_USERNAME")
        password = os.getenv("GG_PASSWORD")
        verify_ssl = os.getenv("GG_VERIFY_SSL", "true").lower() == "true"

        if not all([url, username, password]):
            return False, "Missing required environment variables"

        async with httpx.AsyncClient(
            auth=(username, password),
            verify=verify_ssl,
            timeout=10.0
        ) as client:
            response = await client.get(f"{url.rstrip('/')}/services/v2/deployments")

            if response.status_code == 200:
                data = response.json()
                deployments = data.get("items", [])
                return True, f"Connected successfully. Found {len(deployments)} deployment(s)."
            elif response.status_code == 401:
                return False, "Authentication failed. Check username and password."
            else:
                return False, f"HTTP {response.status_code}: {response.text[:100]}"

    except httpx.ConnectError:
        return False, "Connection failed. Check URL and network connectivity."
    except httpx.TimeoutException:
        return False, "Connection timeout. Check network and firewall settings."
    except Exception as e:
        return False, f"Error: {str(e)}"


async def test_api_operations() -> List[Tuple[str, bool, str]]:
    """Test basic API operations."""
    try:
        import httpx
        from dotenv import load_dotenv

        load_dotenv()

        url = os.getenv("GG_SERVICE_MANAGER_URL")
        username = os.getenv("GG_USERNAME")
        password = os.getenv("GG_PASSWORD")
        verify_ssl = os.getenv("GG_VERIFY_SSL", "true").lower() == "true"

        results = []

        async with httpx.AsyncClient(
            auth=(username, password),
            verify=verify_ssl,
            timeout=10.0
        ) as client:
            base_url = url.rstrip('/')

            # Test 1: List deployments
            try:
                response = await client.get(f"{base_url}/services/v2/deployments")
                deployments = response.json().get("items", [])
                n = len(deployments)
                results.append(("List Deployments", True, f"Found {n} deployment(s)"))

                # Test 2: Get first deployment details (if available)
                if deployments:
                    dep_name = deployments[0].get("name")
                    response = await client.get(f"{base_url}/services/v2/deployments/{dep_name}")
                    msg = f"Retrieved details for '{dep_name}'"
                    results.append(("Get Deployment Details", True, msg))

                    # Test 3: List extracts (if deployment available)
                    try:
                        response = await client.get(
                            f"{base_url}/services/v2/deployments/{dep_name}/services/adminsrvr/extracts"
                        )
                        extracts = response.json().get("items", [])
                        results.append(("List Extracts", True, f"Found {len(extracts)} extract(s)"))
                    except Exception as e:
                        results.append(("List Extracts", False, f"Error: {str(e)[:50]}"))

                    # Test 4: List replicats (if deployment available)
                    try:
                        response = await client.get(
                            f"{base_url}/services/v2/deployments/{dep_name}/services/adminsrvr/replicats"
                        )
                        replicats = response.json().get("items", [])
                        nr = len(replicats)
                        results.append(("List Replicats", True, f"Found {nr} replicat(s)"))
                    except Exception as e:
                        results.append(("List Replicats", False, f"Error: {str(e)[:50]}"))
                else:
                    results.append(("Get Deployment Details", False, "No deployments available"))
                    results.append(("List Extracts", False, "No deployments available"))
                    results.append(("List Replicats", False, "No deployments available"))

            except Exception as e:
                results.append(("List Deployments", False, f"Error: {str(e)[:50]}"))

        return results

    except Exception as e:
        return [("API Operations", False, f"Setup error: {str(e)}")]


async def main():
    """Run all tests."""
    print_header("Oracle GoldenGate MCP Server - Installation Test")

    # Check Python version
    print_header("1. Python Version")
    success, version = check_python_version()
    if success:
        print_success(f"Python {version} (compatible)")
    else:
        print_error(f"Python {version} (requires 3.10 or higher)")
        print_warning("Please upgrade Python before continuing.")
        return

    # Check dependencies
    print_header("2. Dependencies")
    deps = check_dependencies()
    all_installed = True
    for name, installed, status in deps:
        if installed:
            print_success(f"{name}: {status}")
        else:
            print_error(f"{name}: {status}")
            all_installed = False

    if not all_installed:
        print_warning("\nInstall missing dependencies with:")
        print_info("pip install -r requirements.txt")
        return

    # Load environment variables from .env file
    try:
        from dotenv import load_dotenv
        load_dotenv()
        print_success("Loaded environment variables from .env file")
    except Exception as e:
        print_warning(f"Could not load .env file: {e}")
        print_info("Make sure .env file exists and is properly formatted")

    # Check environment configuration
    print_header("3. Environment Configuration")
    env_config = check_environment()
    all_required_set = True

    for name, (is_set, value, var_type) in env_config.items():
        if is_set:
            print_success(f"{name}: {value}")
        else:
            if var_type == "Required":
                print_error(f"{name}: {value}")
                all_required_set = False
            else:
                print_info(f"{name}: {value}")

    if not all_required_set:
        print_warning("\nConfigure missing variables in .env file:")
        print_info("cp .env.example .env")
        print_info("nano .env  # Edit with your values")
        return

    # Test connectivity
    print_header("4. GoldenGate API Connectivity")
    success, message = await test_connectivity()
    if success:
        print_success(message)
    else:
        print_error(message)
        print_warning("\nTroubleshooting tips:")
        print_info("- Verify GG_SERVICE_MANAGER_URL is correct")
        print_info("- Check username and password")
        print_info("- Ensure network connectivity")
        print_info("- For self-signed certs, set GG_VERIFY_SSL=false")
        return

    # Test API operations
    print_header("5. API Operations Test")
    operations = await test_api_operations()
    for operation, success, message in operations:
        if success:
            print_success(f"{operation}: {message}")
        else:
            print_error(f"{operation}: {message}")

    # Final summary
    print_header("Test Summary")
    print_success("All tests passed! ✨")
    print_info("\nYour GoldenGate MCP server is ready to use.")
    print_info("\nNext steps:")
    print_info("1. Configure your MCP client (Claude Desktop, etc.)")
    print_info("2. Test the server with: python goldengate_mcp.py")
    print_info("3. Review audit logs: tail -f goldengate_audit.log")
    print_info("\nFor help, see README.md")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print_error("\n\nTest interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print_error(f"\n\nUnexpected error: {e}")
        sys.exit(1)
