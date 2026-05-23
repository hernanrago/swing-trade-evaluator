# tests/test_intraday_skills.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))

import pytest
from unittest.mock import patch, call, MagicMock


def _fake_candles(n=200):
    return [[i, 100.0, 105.0, 95.0, 102.0, 1000.0] for i in range(n)]


def test_tf_trend_swing_mode_fetches_1d_and_1w(monkeypatch):
    """In swing mode, fetch_klines must be called with '1d' and '1w' intervals."""
    monkeypatch.setenv("TRADE_MODE", "swing")
    candles = _fake_candles(200)

    with patch("evaluate_tf_trend.fetch_klines", return_value=candles) as mock_fetch:
        import evaluate_tf_trend
        evaluate_tf_trend.evaluate_tf_trend("BTC")

    intervals = [c.args[1] for c in mock_fetch.call_args_list]
    assert "1d" in intervals
    assert "1w" in intervals


def test_tf_trend_intraday_mode_fetches_4h_and_1h(monkeypatch):
    """In intraday mode, fetch_klines must be called with '4h' and '1h' intervals."""
    monkeypatch.setenv("TRADE_MODE", "intraday")
    candles = _fake_candles(200)

    with patch("evaluate_tf_trend.fetch_klines", return_value=candles) as mock_fetch:
        import evaluate_tf_trend
        evaluate_tf_trend.evaluate_tf_trend("BTC")

    intervals = [c.args[1] for c in mock_fetch.call_args_list]
    assert "4h" in intervals
    assert "1h" in intervals
    assert "1d" not in intervals
    assert "1w" not in intervals


def test_tf_trend_intraday_result_has_trend_4h_and_trend_1h(monkeypatch):
    """Intraday result must use trend_4h / trend_1h keys."""
    monkeypatch.setenv("TRADE_MODE", "intraday")
    candles = _fake_candles(200)

    with patch("evaluate_tf_trend.fetch_klines", return_value=candles):
        import evaluate_tf_trend
        result = evaluate_tf_trend.evaluate_tf_trend("BTC")

    assert "trend_4h" in result
    assert "trend_1h" in result
    assert "trend_1d" not in result
    assert "trend_1w" not in result


def test_tf_trend_swing_result_has_trend_1d_and_trend_1w(monkeypatch):
    """Swing result must keep trend_1d / trend_1w keys."""
    monkeypatch.setenv("TRADE_MODE", "swing")
    candles = _fake_candles(200)

    with patch("evaluate_tf_trend.fetch_klines", return_value=candles):
        import evaluate_tf_trend
        result = evaluate_tf_trend.evaluate_tf_trend("BTC")

    assert "trend_1d" in result
    assert "trend_1w" in result


from evaluate_market_structure import get_candles, find_pivots, build_swing_sequence, classify_timeframe


def _make_okx_candle(ts, high, low):
    mid = str((high + low) / 2)
    return [str(ts), mid, str(high), str(low), mid, "1000", "1000", "1000000", "1"]


def _okx_mock_resp(n=200, high=85000, low=84000):
    candles = [_make_okx_candle(i * 1000, high + i, low + i) for i in range(n)]
    candles.reverse()  # OKX returns newest first
    mock = MagicMock()
    mock.json.return_value = {"code": "0", "data": candles}
    mock.raise_for_status = MagicMock()
    return mock


def test_market_structure_swing_uses_4h_and_1d(monkeypatch):
    """In swing mode, OKX is queried with bars '4H' and '1D'."""
    monkeypatch.setenv("TRADE_MODE", "swing")

    with patch("evaluate_market_structure.requests.get", return_value=_okx_mock_resp()) as mock_get:
        import evaluate_market_structure
        evaluate_market_structure._CANDLES_CACHE.clear()
        evaluate_market_structure.evaluate_market_structure("BTC")

    bars = [c.kwargs["params"]["bar"] for c in mock_get.call_args_list]
    assert "4H" in bars
    assert "1D" in bars
    assert "1H" not in bars
    assert "15m" not in bars


def test_market_structure_intraday_uses_1h_and_15m(monkeypatch):
    """In intraday mode, OKX is queried with bars '1H' and '15m'."""
    monkeypatch.setenv("TRADE_MODE", "intraday")

    with patch("evaluate_market_structure.requests.get", return_value=_okx_mock_resp()) as mock_get:
        import evaluate_market_structure
        evaluate_market_structure._CANDLES_CACHE.clear()
        evaluate_market_structure.evaluate_market_structure("BTC")

    bars = [c.kwargs["params"]["bar"] for c in mock_get.call_args_list]
    assert "1H" in bars
    assert "15m" in bars
    assert "4H" not in bars
    assert "1D" not in bars


def test_market_structure_intraday_result_has_1h_and_15m_keys(monkeypatch):
    """Intraday result dict must have '1h' and '15m' keys, not '4h'/'1d'."""
    monkeypatch.setenv("TRADE_MODE", "intraday")

    with patch("evaluate_market_structure.requests.get", return_value=_okx_mock_resp()):
        import evaluate_market_structure
        evaluate_market_structure._CANDLES_CACHE.clear()
        result = evaluate_market_structure.evaluate_market_structure("BTC")

    assert "1h" in result
    assert "15m" in result
    assert "4h" not in result
    assert "1d" not in result


def test_market_structure_swing_result_has_4h_and_1d_keys(monkeypatch):
    """Swing result dict must keep '4h' and '1d' keys."""
    monkeypatch.setenv("TRADE_MODE", "swing")

    with patch("evaluate_market_structure.requests.get", return_value=_okx_mock_resp()):
        import evaluate_market_structure
        evaluate_market_structure._CANDLES_CACHE.clear()
        result = evaluate_market_structure.evaluate_market_structure("BTC")

    assert "4h" in result
    assert "1d" in result
