"""Shared test fixtures."""

import pytest

from mealrunner.db import ensure_db


@pytest.fixture
def conn():
    """In-memory database seeded with test data."""
    db = ensure_db(":memory:")
    yield db
    db.close()
