import pytest


@pytest.fixture(autouse=True)
def clear_candles_cache():
    """Clear the candles cache before and after each test."""
    import evaluate_market_structure as ms
    ms._CANDLES_CACHE.clear()
    yield
    ms._CANDLES_CACHE.clear()
