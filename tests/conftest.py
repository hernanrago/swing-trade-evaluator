import pytest
import sys
import os

# Add skills directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))


@pytest.fixture(autouse=True)
def reset_trade_mode(monkeypatch):
    """Reset TRADE_MODE to 'swing' (default) before each test."""
    monkeypatch.setenv("TRADE_MODE", "swing")


@pytest.fixture(autouse=True)
def clear_candles_cache():
    """Clear the candles cache before and after each test."""
    try:
        import evaluate_market_structure as ms
        ms._CANDLES_CACHE.clear()
        yield
        ms._CANDLES_CACHE.clear()
    except ModuleNotFoundError:
        # evaluate_market_structure may not be available in all test environments
        yield
