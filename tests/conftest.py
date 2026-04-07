"""Shared test fixtures."""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force mock mode and test mode for all tests
os.environ["MOCK_TOOLS"] = "true"
os.environ["TEST_MODE"] = "true"


@pytest.fixture
def config():
    from config import Config
    return Config(mock_tools=True)


@pytest.fixture
def db(config):
    """Create a fresh in-memory DB for each test."""
    from database.db import Database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    database = Database(db_path)
    yield database
    database.close()
    os.unlink(db_path)


@pytest.fixture
def registry():
    from tools.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.discover(mock_mode=True)
    return r


@pytest.fixture
def sample_contract():
    from datetime import datetime
    from database.models import Contract
    return Contract(
        id=1,
        source="kalshi",
        source_id="TEST-CONTRACT-1",
        title="Will the Fed cut rates by June 2026?",
        category="economics",
        yes_price=0.62,
        volume_24h=45000,
        open_time=datetime(2026, 1, 15, 12, 0, 0),
        close_time=datetime(2026, 6, 30, 20, 0, 0),
    )


@pytest.fixture
def sample_contract_crypto():
    from datetime import datetime
    from database.models import Contract
    return Contract(
        id=2,
        source="kalshi",
        source_id="BTC-100K-26APR",
        title="Will Bitcoin exceed $100K by April 30, 2026?",
        category="crypto",
        yes_price=0.45,
        volume_24h=89000,
        open_time=datetime(2026, 2, 1),
        close_time=datetime(2026, 4, 30, 23, 59, 59),
    )
