"""
Configuration management for GoldenGate MCP Server.

Supports environment variables, config files, and secure credential management.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

# Matches Jira (OPS-123) and ServiceNow (CHG0012345) style references.
DEFAULT_CHANGE_TICKET_PATTERN = r"[A-Za-z][A-Za-z0-9_]*-?\d+"


def env_bool(name: str, default: bool) -> bool:
    """Boolean env var. Anything unrecognised is an error: GG_READ_ONLY=ture must not
    quietly enable writes, nor VERIFY_SSL=1 quietly disable TLS checks."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}")


def env_list(name: str) -> List[str]:
    """Comma-separated env var as a list, blanks dropped."""
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def read_secret_file(path: str) -> str:
    """Contents of a mounted secret (Docker/Kubernetes secrets end in a newline)."""
    return Path(path).read_text(encoding="utf-8").strip()


def env_secret(name: str) -> Optional[str]:
    """``name``, or the contents of the file at ``<name>_FILE``."""
    secret_file = os.getenv(f"{name}_FILE")
    if secret_file:
        return read_secret_file(secret_file)
    return os.getenv(name)


def _entry_password(dep: Dict[str, Any]) -> str:
    """``password`` of a JSON deployment entry, or the contents of its ``password_file``."""
    if dep.get("password_file"):
        return read_secret_file(dep["password_file"])
    return dep["password"]


@dataclass
class DeploymentConfig:
    """Configuration for a single GoldenGate deployment."""
    name: str
    base_url: str
    username: str
    password: str = field(repr=False)
    verify_ssl: bool = True
    ca_bundle: Optional[str] = None  # Path to CA certificate bundle (.pem/.crt)
    # Real GoldenGate deployment name used in /services/{deployment}/adminsrvr/... URLs.
    # Defaults to `name`; set it when `name` is only a friendly alias.
    gg_deployment: Optional[str] = None

    @property
    def service_name(self) -> str:
        return self.gg_deployment or self.name


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

    audit_log_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Rotate the audit log when it reaches this size (0 disables rotation)",
        ge=0,
    )

    audit_log_backup_count: int = Field(
        default=5,
        description="Number of rotated audit log files to keep",
        ge=0,
    )

    metrics_db_path: str = Field(
        default="./data/metrics.db",
        description="Path to metrics SQLite database"
    )

    enable_metrics: bool = Field(
        default=False,
        description="Enable background metrics collection and SQLite storage. "
        "Leave false (the default) for a lightweight, pure REST pass-through mode."
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

    # Governance
    backup_dir: str = Field(
        default="./backups",
        description="Directory for redacted config snapshots; compare_deployment_configs "
        "only reads files inside it"
    )

    write_deployments: List[str] = Field(
        default_factory=list,
        description="Deployments that accept write tools (empty = all, when not read-only)"
    )

    protected_deployments: List[str] = Field(
        default_factory=list,
        description="Deployments whose write tools require a change_ticket"
    )

    disabled_tools: List[str] = Field(
        default_factory=list,
        description="Tools turned off for everyone"
    )

    max_batch_size: int = Field(
        default=25,
        description="Most processes one batch tool call may touch",
        ge=1,
    )

    change_ticket_pattern: str = Field(
        default=DEFAULT_CHANGE_TICKET_PATTERN,
        description="Regex a change_ticket must fully match"
    )

    # Logging
    log_level: str = Field(default="INFO", description="Python log level")

    log_format: str = Field(
        default="text",
        description="'text' or 'json' (one object per line, for log shippers)",
        pattern="^(text|json)$",
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
        names = [d.name for d in v]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"Duplicate deployment names: {', '.join(duplicates)}")
        return v

    @field_validator('change_ticket_pattern')
    @classmethod
    def validate_change_ticket_pattern(cls, v):
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Invalid change_ticket_pattern: {e}") from e
        return v

    @model_validator(mode='after')
    def validate_policy_deployments(self):
        """A typo here would silently leave a production deployment unprotected."""
        known = {d.name for d in self.deployments}
        for setting in ("write_deployments", "protected_deployments"):
            unknown = sorted(set(getattr(self, setting)) - known)
            if unknown:
                raise ValueError(f"{setting} names unknown deployments: {', '.join(unknown)}")
        return self

    @classmethod
    def from_env(cls) -> "Config":
        """
        Load configuration from environment variables.

        Environment variables:
        - GG_READ_ONLY: Set to 'false' to enable write operations (default: true)
        - GG_REQUEST_TIMEOUT: Request timeout in seconds (default: 30)
        - GG_AUDIT_LOG_PATH: Path to audit log (default: ./logs/audit.log)
        - GG_AUDIT_LOG_MAX_BYTES: Rotate audit log at this size (default: 10 MB, 0 = never)
        - GG_AUDIT_LOG_BACKUP_COUNT: Rotated audit files to keep (default: 5)
        - GG_BACKUP_DIR: Directory for config snapshots (default: ./backups)
        - GG_WRITE_DEPLOYMENTS: Comma-separated deployments that accept writes (default: all)
        - GG_PROTECTED_DEPLOYMENTS: Comma-separated deployments requiring a change_ticket
        - GG_DISABLED_TOOLS: Comma-separated tools to turn off
        - GG_MAX_BATCH_SIZE: Most processes per batch call (default: 25)
        - GG_CHANGE_TICKET_PATTERN: Regex a change_ticket must fully match
        - GG_LOG_LEVEL: Log level (default: INFO)
        - GG_LOG_FORMAT: text or json (default: text)
        - GG_DEPLOYMENTS: JSON string with deployment configurations

        Or individual deployment configs:
        - GG_DEPLOYMENT_<N>_NAME: Single deployment name
        - GG_DEPLOYMENT_<N>_NAMES: Comma-separated names sharing the same URL/credentials
        - GG_DEPLOYMENT_<N>_URL: Base URL
        - GG_DEPLOYMENT_<N>_USERNAME: Username
        - GG_DEPLOYMENT_<N>_PASSWORD: Password (or GG_DEPLOYMENT_<N>_PASSWORD_FILE: path to it)
        - GG_DEPLOYMENT_<N>_VERIFY_SSL: Verify SSL (default: true)
        - GG_DEPLOYMENT_<N>_GG_DEPLOYMENT: Real GoldenGate deployment name when NAME is
          an alias (only valid with a single NAME)

        Returns:
            Config instance
        """
        # Basic settings
        read_only = env_bool("GG_READ_ONLY", True)
        enable_metrics = env_bool("GG_ENABLE_METRICS", False)
        request_timeout = int(os.getenv("GG_REQUEST_TIMEOUT", "30"))
        audit_log_path = os.getenv("GG_AUDIT_LOG_PATH", "./logs/audit.log")
        audit_log_max_bytes = int(os.getenv("GG_AUDIT_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
        audit_log_backup_count = int(os.getenv("GG_AUDIT_LOG_BACKUP_COUNT", "5"))
        metrics_db_path = os.getenv("GG_METRICS_DB_PATH", "./data/metrics.db")
        cache_ttl_seconds = int(os.getenv("GG_CACHE_TTL_SECONDS", "30"))
        max_concurrent_requests = int(os.getenv("GG_MAX_CONCURRENT_REQUESTS", "10"))
        requests_per_second = int(os.getenv("GG_REQUESTS_PER_SECOND", "20"))
        governance = dict(
            backup_dir=os.getenv("GG_BACKUP_DIR", "./backups"),
            write_deployments=env_list("GG_WRITE_DEPLOYMENTS"),
            protected_deployments=env_list("GG_PROTECTED_DEPLOYMENTS"),
            disabled_tools=env_list("GG_DISABLED_TOOLS"),
            max_batch_size=int(os.getenv("GG_MAX_BATCH_SIZE", "25")),
            change_ticket_pattern=os.getenv(
                "GG_CHANGE_TICKET_PATTERN", DEFAULT_CHANGE_TICKET_PATTERN
            ),
            log_level=os.getenv("GG_LOG_LEVEL", "INFO").upper(),
            log_format=os.getenv("GG_LOG_FORMAT", "text").lower(),
        )

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
                    gg_deployment = dep.get("gg_deployment")
                    if gg_deployment and len(names) > 1:
                        raise ValueError("gg_deployment cannot be combined with multiple names")
                    for dep_name in names:
                        deployments.append(DeploymentConfig(
                            name=dep_name.strip(),
                            base_url=dep["base_url"],
                            username=dep["username"],
                            password=_entry_password(dep),
                            verify_ssl=dep.get("verify_ssl", True),
                            ca_bundle=dep.get("ca_bundle"),
                            gg_deployment=gg_deployment,
                        ))
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid GG_DEPLOYMENTS JSON: {e}") from e

        # Option 2: Load individual deployment configs
        else:
            i = 1
            while True:
                # Support NAMES (comma-separated) or NAME (single) for sharing URL/credentials
                names_raw = (
                    os.getenv(f"GG_DEPLOYMENT_{i}_NAMES")
                    or os.getenv(f"GG_DEPLOYMENT_{i}_NAME")
                )
                if not names_raw:
                    break

                base_url = os.getenv(f"GG_DEPLOYMENT_{i}_URL")
                username = os.getenv(f"GG_DEPLOYMENT_{i}_USERNAME")
                password = env_secret(f"GG_DEPLOYMENT_{i}_PASSWORD")
                verify_ssl = env_bool(f"GG_DEPLOYMENT_{i}_VERIFY_SSL", True)
                ca_bundle = os.getenv(f"GG_DEPLOYMENT_{i}_CA_BUNDLE")
                gg_deployment = os.getenv(f"GG_DEPLOYMENT_{i}_GG_DEPLOYMENT")

                if not all([base_url, username, password]):
                    raise ValueError(f"Incomplete configuration for deployment {i}")

                dep_names = [n.strip() for n in names_raw.split(",") if n.strip()]
                if gg_deployment and len(dep_names) > 1:
                    raise ValueError(
                        f"GG_DEPLOYMENT_{i}_GG_DEPLOYMENT cannot be combined with multiple NAMES"
                    )

                for dep_name in dep_names:
                    deployments.append(DeploymentConfig(
                        name=dep_name,
                        base_url=base_url,
                        username=username,
                        password=password,
                        verify_ssl=verify_ssl,
                        ca_bundle=ca_bundle,
                        gg_deployment=gg_deployment,
                    ))

                i += 1

        return cls(
            read_only=read_only,
            enable_metrics=enable_metrics,
            request_timeout=request_timeout,
            audit_log_path=audit_log_path,
            audit_log_max_bytes=audit_log_max_bytes,
            audit_log_backup_count=audit_log_backup_count,
            metrics_db_path=metrics_db_path,
            cache_ttl_seconds=cache_ttl_seconds,
            max_concurrent_requests=max_concurrent_requests,
            requests_per_second=requests_per_second,
            deployments=deployments,
            **governance,
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
                password=_entry_password(dep),
                verify_ssl=dep.get("verify_ssl", True),
                ca_bundle=dep.get("ca_bundle"),
                gg_deployment=dep.get("gg_deployment"),
            ))

        settings = {k: v for k, v in data.items() if k != "deployments"}
        return cls(deployments=deployments, **settings)

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
            if dep.ca_bundle:
                entry["ca_bundle"] = dep.ca_bundle
            if dep.gg_deployment:
                entry["gg_deployment"] = dep.gg_deployment
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
