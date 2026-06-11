"""
ChainWatch Test Shared Fixtures
================================
Pytest conftest.py — provides auto-restore fixtures for mutable module-level
state that tests may mutate. This prevents test isolation bugs where one test's
mutations leak into subsequent tests.

Pattern: Every module-level dict/list/set that tests read or mutate should have
a corresponding fixture that deep-copies it before the test and restores it after.
"""
import copy
import os
import pytest

# Set JWT_SECRET before any test imports main.py (module-level check)
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only")


@pytest.fixture(autouse=True)
def _restore_field_contract_responses():
    """
    Auto-restore ENDPOINT_RESPONSES after every test in the field_contract test
    module. This prevents shallow-copy bugs where a test mutates nested sets
    (e.g., discarding a field from wallet_meta) and the mutation leaks into
    subsequent tests.

    Uses deepcopy to ensure nested structures (dicts of sets) are fully
    independent copies.
    """
    import services.field_contract as fc

    # Deep copy the entire ENDPOINT_RESPONSES list (contains nested dicts/sets)
    original = copy.deepcopy(fc.ENDPOINT_RESPONSES)
    yield
    # Restore after test completes (even if test fails)
    fc.ENDPOINT_RESPONSES.clear()
    fc.ENDPOINT_RESPONSES.extend(copy.deepcopy(original))


@pytest.fixture(autouse=True)
def _restore_signal_dedup_cache():
    """
    Auto-restore the signal dedup cache in signal_generator after every test.
    Tests that insert signals will populate this cache, and without cleanup,
    subsequent tests may see false duplicates.
    """
    from services import signal_generator as sg

    original_cache = copy.deepcopy(sg._signal_dedup_cache)
    yield
    sg._signal_dedup_cache.clear()
    sg._signal_dedup_cache.update(copy.deepcopy(original_cache))


@pytest.fixture(autouse=True)
def _restore_alert_cooldown_cache():
    """
    Auto-restore the alert cooldown cache in alert_evaluator after every test.
    Tests that fire alerts will populate this cache, and without cleanup,
    subsequent tests may see false cooldown hits.
    """
    from services import alert_evaluator as ae

    original_cache = copy.deepcopy(ae._cooldown_cache)
    original_prune_ts = ae._last_cooldown_prune
    yield
    ae._cooldown_cache.clear()
    ae._cooldown_cache.update(copy.deepcopy(original_cache))
    ae._last_cooldown_prune = original_prune_ts


@pytest.fixture
def test_client():
    """
    Provide a FastAPI TestClient for integration tests.
    Uses a fresh app instance to avoid state pollution from the running server.
    """
    from fastapi.testclient import TestClient as _TestClient
    from main import app
    with _TestClient(app) as client:
        yield client
