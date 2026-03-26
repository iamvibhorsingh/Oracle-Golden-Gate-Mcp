# GoldenGate MCP Server Tests

This directory contains comprehensive tests for the GoldenGate MCP Server.

## Test Structure

```
tests/
├── conftest.py                    # Shared fixtures and configuration
├── test_connection.py             # Connection and basic functionality tests
├── test_installation.py           # Installation verification tests
├── test_metrics_store.py          # MetricsStore tests (Phase 1)
├── test_diagnostics.py            # DiagnosticsEngine tests (Phase 1)
├── test_database_monitor.py       # DatabaseMonitor tests
├── test_models.py                 # Severity / structured output helpers
├── test_goldengate_client.py      # HTTP client retries and errors
├── test_config.py                 # Config.from_env / from_file edge cases
├── test_metrics_collection_round.py  # Background metrics collection round
├── integration/README.md          # Live GoldenGate tests (manual; marker requires_gg)
└── README.md                      # This file
```

## Running Tests

### Run All Tests

```bash
# From project root
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=src/goldengate_mcp_server --cov-report=html
```

### Run Specific Test Files

```bash
# Test metrics store
pytest tests/test_metrics_store.py -v

# Test diagnostics
pytest tests/test_diagnostics.py -v

# Test database monitor
pytest tests/test_database_monitor.py -v

# Test HTTP client
pytest tests/test_goldengate_client.py -v
```

### Run Specific Test Classes or Methods

```bash
# Run a specific test class
pytest tests/test_metrics_store.py::TestMetricsStore -v

# Run a specific test method
pytest tests/test_metrics_store.py::TestMetricsStore::test_record_lag_metric -v
```

## Test Categories

### Unit Tests
- `test_metrics_store.py` - Tests for historical metrics storage
- `test_diagnostics.py` - Tests for diagnostic engine
- `test_database_monitor.py` - Tests for database monitoring
- `test_models.py` - Classification and parsing helpers
- `test_goldengate_client.py` - REST client behavior

### Integration Tests
- `test_connection.py` - Tests for GoldenGate API connectivity
- `test_installation.py` - Tests for package installation

## Requirements

Install test dependencies:

```bash
pip install -e ".[dev]"
```

Or install individually:

```bash
pip install pytest pytest-asyncio pytest-cov
```

## Writing New Tests

### Test File Template

```python
"""
Tests for [Module Name].
"""

import pytest
from goldengate_mcp_server.[module] import [Class]


@pytest.fixture
def sample_fixture():
    """Create a sample fixture."""
    return [Class]()


class Test[ClassName]:
    """Test suite for [ClassName]."""
    
    def test_something(self, sample_fixture):
        """Test something specific."""
        assert sample_fixture is not None
```

### Async Test Template

```python
@pytest.mark.asyncio
async def test_async_function():
    """Test an async function."""
    result = await some_async_function()
    assert result is not None
```

## Coverage

Generate coverage report:

```bash
pytest tests/ --cov=src/goldengate_mcp_server --cov-report=html
```

View coverage report:

```bash
# Open htmlcov/index.html in your browser
```

## Continuous Integration

These tests are designed to run in CI/CD pipelines. Example GitHub Actions workflow:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      - run: pip install -e ".[dev]"
      - run: pytest tests/ -v --cov
```

## Mocking

Tests use `unittest.mock` for mocking external dependencies:

- **AsyncMock**: For async functions
- **MagicMock**: For regular functions and objects
- **patch**: For patching modules and functions

Example:

```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_with_mock():
    with patch('module.function') as mock_func:
        mock_func.return_value = "mocked"
        result = await some_function()
        assert result == "mocked"
```

## Test Data

Test data is managed through fixtures in `conftest.py`. Temporary directories are automatically created and cleaned up.

## Troubleshooting

### Import Errors

If you get import errors, ensure the package is installed in development mode:

```bash
pip install -e .
```

### Async Test Failures

Ensure `pytest-asyncio` is installed:

```bash
pip install pytest-asyncio
```

### Database Tests

Database tests use temporary SQLite databases that are automatically cleaned up. No external database is required.

## Best Practices

1. **One assertion per test** (when possible)
2. **Use descriptive test names** that explain what is being tested
3. **Use fixtures** for common setup
4. **Mock external dependencies** (databases, APIs, etc.)
5. **Test edge cases** and error conditions
6. **Keep tests fast** - use mocks instead of real connections
7. **Test both success and failure paths**
