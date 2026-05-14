import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def clear_candles_cache():
    """Clear the candles cache before and after each test."""
    import evaluate_market_structure as ms
    ms._CANDLES_CACHE.clear()
    yield
    ms._CANDLES_CACHE.clear()


# ── Task 1: candle cache ────────────────────────────────────────────────────

def _okx_candles_response(n=5):
    """Returns a minimal OKX candles API response with n candles."""
    candles = [[str(i), "100", "110", "90", "105", "1000", "1"] for i in range(n, 0, -1)]
    return MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": {"code": "0", "data": candles},
    })

def test_get_candles_caches_second_call():
    import evaluate_market_structure as ms
    mock_resp = _okx_candles_response()
    with patch("evaluate_market_structure.requests.get", return_value=mock_resp) as mock_get:
        ms.get_candles("BTC-USDT-SWAP", "4H", 5)
        ms.get_candles("BTC-USDT-SWAP", "4H", 5)   # second call — should hit cache
    assert mock_get.call_count == 1                  # only one real HTTP call
