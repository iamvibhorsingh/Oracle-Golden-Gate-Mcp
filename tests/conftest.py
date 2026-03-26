"""
Pytest configuration and shared fixtures.
"""

import sys
from pathlib import Path

import pytest

# Add src directory to path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


@pytest.fixture(scope="session")
def test_data_dir(tmp_path_factory):
    """Create a temporary directory for test data."""
    return tmp_path_factory.mktemp("test_data")


@pytest.fixture
def sample_lag_data():
    """Sample lag data for testing."""
    return {
        "lag": "00:00:05",
        "lag_at_chkpt": "00:00:03",
        "time_since_chkpt": "00:00:10",
        "status": "running"
    }


@pytest.fixture
def sample_process_status():
    """Sample process status for testing."""
    return {
        "name": "TEST_PROCESS",
        "status": "running",
        "type": "INTEGRATED",
        "lag": "00:00:05"
    }


@pytest.fixture
def sample_health_data():
    """Sample health data for testing."""
    return {
        "deployment": "test_deployment",
        "timestamp": "2024-01-01T00:00:00",
        "overall_status": "healthy",
        "extracts": [
            {
                "name": "EXT1",
                "status": "running",
                "lag": {"lag": "00:00:05"}
            }
        ],
        "replicats": [
            {
                "name": "REP1",
                "status": "running",
                "lag": {"lag": "00:00:03"}
            }
        ],
        "issues": []
    }
