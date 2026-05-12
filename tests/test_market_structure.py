# tests/test_market_structure.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))

import pytest
from evaluate_market_structure import _normalize_pair

def test_normalize_bare_symbol():
    assert _normalize_pair("BTC") == "BTC-USDT-SWAP"

def test_normalize_usdt_suffix():
    assert _normalize_pair("BTCUSDT") == "BTC-USDT-SWAP"

def test_normalize_dash_usdt():
    assert _normalize_pair("BTC-USDT") == "BTC-USDT-SWAP"

def test_normalize_full_swap():
    assert _normalize_pair("BTC-USDT-SWAP") == "BTC-USDT-SWAP"

def test_normalize_lowercase():
    assert _normalize_pair("eth") == "ETH-USDT-SWAP"

def test_normalize_sol_usdt():
    assert _normalize_pair("SOLUSDT") == "SOL-USDT-SWAP"

def test_normalize_underscore_usdt():
    assert _normalize_pair("BTC_USDT") == "BTC-USDT-SWAP"


from unittest.mock import patch, MagicMock
from evaluate_market_structure import get_candles

def _make_okx_candle(ts, high, low, open_=None, close=None):
    """Helper: create candle in OKX format [ts, open, high, low, close, ...]"""
    o = str(open_ or (high + low) / 2)
    c = str(close or (high + low) / 2)
    return [str(ts), o, str(high), str(low), c, "1000", "1000", "1000000", "1"]

def test_get_candles_returns_oldest_first():
    """OKX returns newest first; get_candles must reverse."""
    mock_data = [
        _make_okx_candle(300, 85000, 84000),
        _make_okx_candle(200, 84000, 83000),
        _make_okx_candle(100, 83000, 82000),
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": "0", "data": mock_data}
    mock_resp.raise_for_status = MagicMock()

    with patch("evaluate_market_structure.requests.get", return_value=mock_resp):
        result = get_candles("BTC-USDT-SWAP", "4H", 3)

    assert isinstance(result, list)
    assert result[0][0] == "100"   # oldest first
    assert result[-1][0] == "300"  # newest last

def test_get_candles_api_error():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": "51001", "msg": "Instrument ID does not exist"}
    mock_resp.raise_for_status = MagicMock()

    with patch("evaluate_market_structure.requests.get", return_value=mock_resp):
        result = get_candles("INVALID-USDT-SWAP", "4H", 10)

    assert "error" in result

def test_get_candles_network_error():
    with patch("evaluate_market_structure.requests.get", side_effect=Exception("timeout")):
        result = get_candles("BTC-USDT-SWAP", "4H", 10)

    assert "error" in result


from evaluate_market_structure import find_pivots

def _candles_from_hl(hl_pairs):
    """Build minimal candle list from (high, low) pairs. Timestamps are 1-based."""
    return [_make_okx_candle(i + 1, h, l) for i, (h, l) in enumerate(hl_pairs)]

def test_find_pivots_basic():
    # N=2: candle 2 is SH (high=5 is max in window [0..4])
    # candle 4 is SL (low=1 is min in window [2..6])
    hl = [
        (2, 1), (3, 2), (5, 4),   # SH at index 2
        (4, 3), (3, 1),            # SL at index 4
        (4, 2), (3, 2),            # last 2: excluded
    ]
    candles = _candles_from_hl(hl)
    pivots = find_pivots(candles, n=2)
    sh_prices = [p["price"] for p in pivots if p["type"] == "SH"]
    sl_prices = [p["price"] for p in pivots if p["type"] == "SL"]
    assert 5.0 in sh_prices
    assert 1.0 in sl_prices

def test_find_pivots_excludes_last_n_candles():
    # Last N candles must not appear as pivots even if they look extreme
    hl = [
        (2, 1), (3, 2), (5, 4), (4, 3), (3, 2),  # valid range
        (9, 0), (8, 0),                              # last N=2: must be excluded
    ]
    candles = _candles_from_hl(hl)
    pivots = find_pivots(candles, n=2)
    pivot_indices = [p["index"] for p in pivots]
    assert 5 not in pivot_indices
    assert 6 not in pivot_indices

def test_find_pivots_equal_highs_keeps_most_recent():
    # Two candles share same max in window: keep most recent (index 4, not 3)
    hl = [
        (2, 1), (3, 2), (4, 3), (5, 4), (5, 4), (4, 3), (3, 2),
    ]
    candles = _candles_from_hl(hl)
    pivots = find_pivots(candles, n=2)
    sh_indices = [p["index"] for p in pivots if p["type"] == "SH" and p["price"] == 5.0]
    assert sh_indices == [4]  # only index 4, not 3

def test_find_pivots_returns_sorted_by_time():
    hl = [
        (1, 0), (3, 2), (5, 4), (3, 1), (2, 0), (3, 1), (4, 2),
        (2, 0), (1, 0),
    ]
    candles = _candles_from_hl(hl)
    pivots = find_pivots(candles, n=2)
    indices = [p["index"] for p in pivots]
    assert indices == sorted(indices)
