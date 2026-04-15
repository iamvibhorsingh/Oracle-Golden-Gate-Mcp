"""
Configuration management for GoldenGate MCP Server.

Supports environment variables, config files, and secure credential management.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


@dataclass
class DeploymentConfig:
    """Configuration for a single GoldenGate deployment."""
    name: str
    base_url: str
    username: str
    password: str
    verify_ssl: bool = True
    ca_bundle: Optional[str] = None  # Path to CA certificate bundle (.pem/.crt)


class Config(BaseModel):
    """Main server configuration."""

    # Server settings
    read_only: bool = Field(
        default=True,
        description="Run in read-only mode (no write operations allowed)"
    )

    request_timeout: int = Field(
        default=30,
        description="HTTP request timeout in seconds",
        ge=1,
        le=300
    )

    audit_log_path: str = Field(
        default="./logs/audit.log",
        description="Path to audit log file"
    )

    metrics_db_path: str = Field(
        default="./data/metrics.db",
        description="Path to metrics SQLite database"
    )

    enable_metrics: bool = Field(
        default=False,
        description="Enable background metrics collection and SQLite storage. "
        "Set to false for a lightweight, pure REST pass-through mode."
    )

    # Performance settings
    cache_ttl_seconds: int = Field(
        default=30,
        description="Cache TTL in seconds (protects GoldenGate from repeated queries)",
        ge=0,
        le=300
    )

    max_concurrent_requests: int = Field(
        default=10,
        description="Maximum concurrent requests to GoldenGate API",
        ge=1,
        le=100
    )

    requests_per_second: int = Field(
        default=20,
        description="Maximum requests per second to GoldenGate API",
        ge=1,
        le=100
    )

    # Deployments
    deployments: List[DeploymentConfig] = Field(
        default_factory=list,
        description="List of GoldenGate deployments to manage"
    )

    @field_validator('deployments')
    @classmethod
    def validate_deployments(cls, v):
        """Ensure at least one deployment is configured."""
        if not v:
            raise ValueError("At least one deployment must be configured")
        return v

    @classmethod
    def from_env(cls) -> "Config":
        """
        Load configuration from environment variables.

        Environment variables:
        - GG_READ_ONLY: Set to 'false' to enable write operations (default: true)
        - GG_REQUEST_TIMEOUT: Request timeout in seconds (default: 30)
        - GG_AUDIT_LOG_PATH: Path to audit log (default: ./logs/audit.log)
        - GG_DEPLOYMENTS: JSON string with deployment configurations

        Or individual deployment configs:
        - GG_DEPLOYMENT_<N>_NAME: Single deployment name
        - GG_DEPLOYMENT_<N>_NAMES: Comma-separated names sharing the same URL/credentials
        - GG_DEPLOYMENT_<N>_URL: Base URL
        - GG_DEPLOYMENT_<N>_USERNAME: Username
        - GG_DEPLOYMENT_<N>_PASSWORD: Password
        - GG_DEPLOYMENT_<N>_VERIFY_SSL: Verify SSL (default: true)

        Returns:
            Config instance
        """
        # Basic settings
        read_only = os.getenv("GG_READ_ONLY", "true").lower() == "true"
        enable_metrics = os.getenv("GG_ENABLE_METRICS", "false").lower() == "true"
        request_timeout = int(os.getenv("GG_REQUEST_TIMEOUT", "30"))
        audit_log_path = os.getenv("GG_AUDIT_LOG_PATH", "./logs/audit.log")
        metrics_db_path = os.getenv("GG_METRICS_DB_PATH", "./data/metrics.db")
        cache_ttl_seconds = int(os.getenv("GG_CACHE_TTL_SECONDS", "30"))
        max_concurrent_requests = int(os.getenv("GG_MAX_CONCURRENT_REQUESTS", "10"))
        requests_per_second = int(os.getenv("GG_REQUESTS_PER_SECOND", "20"))

        # Load deployments
        deployments = []

        # Option 1: Load from JSON string
        deployments_json = os.getenv("GG_DEPLOYMENTS")
        if deployments_json:
            try:
                deployments_data = json.loads(deployments_json)
                for dep in deployments_data:
                    # Support "names" array for multiple deployments sharing URL/credentials
                    names = dep.get("names") or [dep["name"]]
                    for dep_name in names:
                        deployments.append(DeploymentConfig(
                            name=dep_name.strip(),
                            base_url=dep["base_url"],
                            username=dep["username"],
                            password=dep["password"],
                            verify_ssl=dep.get("verify_ssl", True),
                            ca_bundle=dep.get("ca_bundle"),
                        ))
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid GG_DEPLOYMENTS JSON: {e}") from e

        # Option 2: Load individual deployment configs
        else:
            i = 1
            while True:
                # Support NAMES (comma-separated) or NAME (single) for sharing URL/credentials
                names_raw = os.getenv(f"GG_DEPLOYMENT_{i}_NAMES") or os.getenv(f"GG_DEPLOYMENT_{i}_NAME")
                if not names_raw:
                    break

                base_url = os.getenv(f"GG_DEPLOYMENT_{i}_URL")
                username = os.getenv(f"GG_DEPLOYMENT_{i}_USERNAME")
                password = os.getenv(f"GG_DEPLOYMENT_{i}_PASSWORD")
                verify_ssl = os.getenv(f"GG_DEPLOYMENT_{i}_VERIFY_SSL", "true").lower() == "true"
                ca_bundle = os.getenv(f"GG_DEPLOYMENT_{i}_CA_BUNDLE")

                if not all([base_url, username, password]):
                    raise ValueError(f"Incomplete configuration for deployment {i}")

                for dep_name in [n.strip() for n in names_raw.split(",") if n.strip()]:
                    deployments.append(DeploymentConfig(
                        name=dep_name,
                        base_url=base_url,
                        username=username,
                        password=password,
                        verify_ssl=verify_ssl,
                        ca_bundle=ca_bundle,
                    ))

                i += 1

        return cls(
            read_only=read_only,
            enable_metrics=enable_metrics,
            request_timeout=request_timeout,
            audit_log_path=audit_log_path,
            metrics_db_path=metrics_db_path,
            cache_ttl_seconds=cache_ttl_seconds,
            max_concurrent_requests=max_concurrent_requests,
            requests_per_second=requests_per_second,
            deployments=deployments
        )

    @classmethod
    def from_file(cls, config_path: str) -> "Config":
        """
        Load configuration from a JSON file.

        Args:
            config_path: Path to configuration file

        Returns:
            Config instance
        """
        with open(config_path, 'r') as f:
            data = json.load(f)

        deployments = []
        for dep in data.get("deployments", []):
            deployments.append(DeploymentConfig(
                name=dep["name"],
                base_url=dep["base_url"],
                username=dep["username"],
                password=dep["password"],
                verify_ssl=dep.get("verify_ssl", True)
            ))

        return cls(
            read_only=data.get("read_only", True),
            request_timeout=data.get("request_timeout", 30),
            audit_log_path=data.get("audit_log_path", "./logs/audit.log"),
            deployments=deployments
        )

    def to_file(self, config_path: str, *, include_secrets: bool = False):
        """
        Save configuration to a JSON file.

        By default passwords are not written; set include_secrets=True only when intentionally
        exporting credentials (avoid for published tooling and shared disks).

        Args:
            config_path: Path to save configuration
            include_secrets: When False, passwords are replaced with placeholders.
        """
        deployments_out = []
        for dep in self.deployments:
            entry = {
                "name": dep.name,
                "base_url": dep.base_url,
                "username": dep.username,
                "verify_ssl": dep.verify_ssl,
            }
            if include_secrets:
                entry["password"] = dep.password
            else:
                entry["password"] = "<set via GG_DEPLOYMENTS or env; not written by to_file>"
            deployments_out.append(entry)

        data = {
            "read_only": self.read_only,
            "request_timeout": self.request_timeout,
            "audit_log_path": self.audit_log_path,
            "deployments": deployments_out,
        }

        Path(config_path).parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, 'w') as f:
            json.dump(data, f, indent=2)


def create_example_config(output_path: str = "./config.example.json"):
    """
    Create an example configuration file.

    Args:
        output_path: Where to save the example config
    """
    example = {
        "read_only": True,
        "request_timeout": 30,
        "audit_log_path": "./logs/audit.log",
        "deployments": [
            {
                "name": "gg21_prod",
                "base_url": "https://gg21-server:9000",
                "username": "oggadmin",
                "password": "your-secure-password",
                "verify_ssl": True
            },
            {
                "name": "gg23_test",
                "base_url": "https://gg23-server:9100",
                "username": "oggadmin",
                "password": "your-secure-password",
                "verify_ssl": True
            }
        ]
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(example, f, indent=2)

    print(f"Example configuration created at: {output_path}")
    print("\nIMPORTANT SECURITY NOTES:")
    print("1. Never commit config files with real passwords to version control")
    print("2. Use environment variables for production deployments")
    print("3. Enable SSL verification in production (verify_ssl: true)")
    print("4. Use strong, unique passwords for each deployment")
    print("5. Restrict file permissions: chmod 600 config.json")


if __name__ == "__main__":
    # Create example config when run directly
    create_example_config()
