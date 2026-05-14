# tests/test_market_structure.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))

import pytest
from evaluate_market_structure import _normalize_pair


@pytest.fixture(autouse=True)
def clear_candles_cache():
    """Clear the candles cache before and after each test."""
    import evaluate_market_structure as ms
    ms._CANDLES_CACHE.clear()
    yield
    ms._CANDLES_CACHE.clear()

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


from evaluate_market_structure import build_swing_sequence

def _pivot(type_, price, ts=1):
    return {"type": type_, "price": float(price), "timestamp": str(ts), "index": ts}

def test_swing_sequence_already_alternating():
    pivots = [_pivot("SH", 5, 1), _pivot("SL", 2, 2), _pivot("SH", 7, 3), _pivot("SL", 4, 4)]
    seq = build_swing_sequence(pivots, tolerance_pct=0.05)
    assert [s["type"] for s in seq] == ["SH", "SL", "SH", "SL"]
    assert [s["price"] for s in seq] == [5.0, 2.0, 7.0, 4.0]

def test_swing_sequence_consecutive_sh_keeps_higher():
    pivots = [_pivot("SH", 5, 1), _pivot("SH", 8, 2), _pivot("SL", 3, 3)]
    seq = build_swing_sequence(pivots, tolerance_pct=0.05)
    assert seq[0]["price"] == 8.0
    assert seq[0]["type"] == "SH"

def test_swing_sequence_consecutive_sl_keeps_lower():
    pivots = [_pivot("SL", 5, 1), _pivot("SL", 2, 2), _pivot("SH", 8, 3)]
    seq = build_swing_sequence(pivots, tolerance_pct=0.05)
    assert seq[0]["price"] == 2.0
    assert seq[0]["type"] == "SL"

def test_swing_sequence_equal_within_tolerance_keeps_most_recent():
    pivots = [_pivot("SH", 5000, 1), _pivot("SH", 5001, 2), _pivot("SL", 3000, 3)]
    seq = build_swing_sequence(pivots, tolerance_pct=0.05)
    assert seq[0]["price"] == 5001.0
    assert seq[0]["timestamp"] == "2"

def test_swing_sequence_empty():
    assert build_swing_sequence([], tolerance_pct=0.05) == []

def test_swing_sequence_single_pivot():
    pivots = [_pivot("SH", 5, 1)]
    seq = build_swing_sequence(pivots, tolerance_pct=0.05)
    assert len(seq) == 1
    assert seq[0]["price"] == 5.0


from evaluate_market_structure import classify_timeframe

def _swings_long():
    return [
        _pivot("SH", 82000, 1), _pivot("SL", 78000, 2),
        _pivot("SH", 85000, 3), _pivot("SL", 80500, 4),
    ]

def _swings_short():
    return [
        _pivot("SH", 85000, 1), _pivot("SL", 80000, 2),
        _pivot("SH", 83000, 3), _pivot("SL", 77000, 4),
    ]

def _swings_undefined():
    return [
        _pivot("SH", 82000, 1), _pivot("SL", 80000, 2),
        _pivot("SH", 85000, 3), _pivot("SL", 77000, 4),
    ]

def test_classify_long_structure():
    result = classify_timeframe(_swings_long(), tolerance_pct=0.05)
    assert result["structure"] == "LONG"
    assert result["high_structure"] == "HH"
    assert result["low_structure"] == "HL"
    assert result["higher_high"] is True
    assert result["higher_low"] is True
    assert result["lower_high"] is False
    assert result["lower_low"] is False
    assert result["invalidation"] == 80500.0
    assert result["range_high"] is None
    assert result["range_low"] is None

def test_classify_short_structure():
    result = classify_timeframe(_swings_short(), tolerance_pct=0.05)
    assert result["structure"] == "SHORT"
    assert result["high_structure"] == "LH"
    assert result["low_structure"] == "LL"
    assert result["invalidation"] == 83000.0
    assert result["range_high"] is None

def test_classify_undefined_mixed():
    result = classify_timeframe(_swings_undefined(), tolerance_pct=0.05)
    assert result["structure"] == "UNDEFINED"
    assert result["invalidation"] is None
    assert result["range_high"] == 85000.0
    assert result["range_low"] == 77000.0
    assert result["reason"] == "mixed_structure"

def test_classify_insufficient_swings():
    result = classify_timeframe([_pivot("SH", 85000, 1), _pivot("SL", 80000, 2)], tolerance_pct=0.05)
    assert result["structure"] == "UNDEFINED"
    assert result["reason"] == "insufficient_swings"

def test_classify_last_swing_fields():
    result = classify_timeframe(_swings_long(), tolerance_pct=0.05)
    assert result["last_swing_high"] == 85000.0
    assert result["last_swing_low"] == 80500.0
    assert len(result["last_swings"]) == 4
    assert result["last_swings"][-1]["type"] == "SL"

def test_classify_equal_high_within_tolerance():
    swings = [
        _pivot("SH", 85000, 1), _pivot("SL", 78000, 2),
        _pivot("SH", 85010, 3), _pivot("SL", 80000, 4),  # 85010 is within 0.05% of 85000
    ]
    result = classify_timeframe(swings, tolerance_pct=0.05)
    assert result["high_structure"] == "EH"
    assert result["structure"] == "UNDEFINED"


from evaluate_market_structure import combine_conclusions, generate_reasoning

def test_combine_long_long():
    assert combine_conclusions("LONG", "LONG") == ("LONG", "high")

def test_combine_short_short():
    assert combine_conclusions("SHORT", "SHORT") == ("SHORT", "high")

def test_combine_long_undefined():
    assert combine_conclusions("LONG", "UNDEFINED") == ("LONG", "moderate")

def test_combine_undefined_short():
    assert combine_conclusions("UNDEFINED", "SHORT") == ("SHORT", "moderate")

def test_combine_conflict():
    assert combine_conclusions("LONG", "SHORT") == ("CONFLICT", "low")

def test_combine_both_undefined():
    assert combine_conclusions("UNDEFINED", "UNDEFINED") == ("UNDEFINED", "low")

def test_generate_reasoning_long_high():
    tf_4h = {"structure": "LONG", "invalidation": 80500}
    tf_1d = {"structure": "LONG", "invalidation": 78000}
    text = generate_reasoning(tf_4h, tf_1d, "LONG", "high")
    assert "4H" in text and "1D" in text
    assert "$80,500" in text
    assert "$78,000" in text

def test_generate_reasoning_conflict():
    tf_4h = {"structure": "LONG", "invalidation": 80500}
    tf_1d = {"structure": "SHORT", "invalidation": 85000}
    text = generate_reasoning(tf_4h, tf_1d, "CONFLICT", "low")
    assert "conflict" in text.lower() or "CONFLICT" in text or "4H" in text


from evaluate_market_structure import evaluate_market_structure

def _make_200_candles(base_high=85000, base_low=80000, count=200):
    """Build a trending candle list with clear alternating pivots."""
    import random
    random.seed(42)
    candles = []
    h = base_high
    for i in range(count):
        direction = 1 if (i // 10) % 2 == 0 else -1
        h = h + direction * random.uniform(50, 200)
        l = h - random.uniform(200, 500)
        candles.append(_make_okx_candle(i + 1, h, max(l, 1)))
    return candles

def test_evaluate_returns_expected_shape():
    """Integration smoke test: mock both API calls, verify output shape."""
    candles_200 = _make_200_candles()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "code": "0",
        "data": list(reversed(candles_200))  # OKX returns newest first
    }

    with patch("evaluate_market_structure.requests.get", return_value=mock_resp):
        result = evaluate_market_structure("BTC")

    assert "pair" in result
    assert "instrument" in result
    assert "4h" in result
    assert "1d" in result
    assert "conclusion" in result
    assert "confidence" in result
    assert "reasoning" in result
    assert result["conclusion"] in ("LONG", "SHORT", "CONFLICT", "UNDEFINED")
    assert result["confidence"] in ("high", "moderate", "low")
    assert result["4h"]["structure"] in ("LONG", "SHORT", "UNDEFINED")
    assert result["1d"]["structure"] in ("LONG", "SHORT", "UNDEFINED")

def test_evaluate_api_error_returns_error_dict():
    with patch("evaluate_market_structure.requests.get", side_effect=Exception("network error")):
        result = evaluate_market_structure("BTC")
    assert "error" in result
