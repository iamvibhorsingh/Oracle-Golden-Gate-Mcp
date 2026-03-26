"""
ENTERPRISE SCALE SIMULATOR for GoldenGate MCP

Use this script to create 'dummy' Extracts on your local GoldenGate Free instance.
This allows you to test how the MCP server and LLM handle deployments with
dozens of processes without needing a real production database.
"""

import os

import requests
from dotenv import load_dotenv

# Load from your .env
load_dotenv()

URL = os.getenv("GG_DEPLOYMENT_1_URL", "https://localhost:9001")
USER = os.getenv("GG_DEPLOYMENT_1_USERNAME", "oggadmin")
PWD = os.getenv("GG_DEPLOYMENT_1_PASSWORD")

def create_dummy_extract(name):
    print(f"Creating dummy extract {name}...")
    endpoint = f"{URL.rstrip('/')}/services/v2/extracts"

    # Minimal JSON for a 'Passive' extract definition
    payload = {
        "name": name,
        "type": "extract",
        "status": "stopped",
        "config": ["-- Dummy Extract for Scale Testing"]
    }

    try:
        r = requests.post(
            endpoint,
            auth=(USER, PWD),
            json=payload,
            verify=False # Local docker usually has self-signed certs
        )
        if r.status_code in (200, 201):
            print(f"  Success: {name}")
        else:
            print(f"  Error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"  Connection Failed: {e}")

if __name__ == "__main__":
    count = 20 # Start with 20 to see how the MCP 'Health Check' feels
    prefix = "TEST_EXT_"

    print(f"Simulating Enterprise Scale on {URL}...")
    for i in range(1, count + 1):
        name = f"{prefix}{i:02}"
        create_dummy_extract(name)

    print("\nScale simulation complete.")
    print("Now ask your MCP: 'Show me the health of all extracts' or 'Check for errors'.")
