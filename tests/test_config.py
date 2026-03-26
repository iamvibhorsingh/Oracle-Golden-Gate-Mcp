"""Config.from_env edge cases (publish roadmap Phase 4)."""

import pytest

from goldengate_mcp_server.config import Config


def test_from_env_single_deployment(monkeypatch):
    monkeypatch.delenv("GG_DEPLOYMENTS", raising=False)
    monkeypatch.setenv("GG_DEPLOYMENT_1_NAME", "prod")
    monkeypatch.setenv("GG_DEPLOYMENT_1_URL", "https://gg:9000")
    monkeypatch.setenv("GG_DEPLOYMENT_1_USERNAME", "admin")
    monkeypatch.setenv("GG_DEPLOYMENT_1_PASSWORD", "secret")
    c = Config.from_env()
    assert len(c.deployments) == 1
    assert c.deployments[0].name == "prod"
    assert c.deployments[0].base_url == "https://gg:9000"


def test_from_env_invalid_gg_deployments_json(monkeypatch):
    monkeypatch.setenv("GG_DEPLOYMENTS", "not-valid-json{")
    with pytest.raises(ValueError, match="Invalid GG_DEPLOYMENTS"):
        Config.from_env()


def test_from_env_incomplete_deployment(monkeypatch):
    monkeypatch.delenv("GG_DEPLOYMENTS", raising=False)
    monkeypatch.setenv("GG_DEPLOYMENT_1_NAME", "only_name")
    monkeypatch.delenv("GG_DEPLOYMENT_1_URL", raising=False)
    monkeypatch.delenv("GG_DEPLOYMENT_1_USERNAME", raising=False)
    monkeypatch.delenv("GG_DEPLOYMENT_1_PASSWORD", raising=False)
    monkeypatch.delenv("GG_DEPLOYMENT_2_NAME", raising=False)
    with pytest.raises(ValueError, match="Incomplete configuration"):
        Config.from_env()


def test_from_file_minimal_json(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(
        """
        {
          "read_only": true,
          "request_timeout": 45,
          "audit_log_path": "./logs/a.log",
          "deployments": [
            {
              "name": "x",
              "base_url": "https://h:1/",
              "username": "u",
              "password": "p",
              "verify_ssl": false
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    c = Config.from_file(str(path))
    assert c.request_timeout == 45
    assert c.deployments[0].verify_ssl is False


def test_config_requires_deployment():
    with pytest.raises(ValueError, match="At least one deployment"):
        Config(
            read_only=True,
            request_timeout=30,
            audit_log_path="./logs/a.log",
            deployments=[],
        )
