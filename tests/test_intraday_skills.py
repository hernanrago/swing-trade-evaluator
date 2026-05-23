# tests/test_intraday_skills.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))

import pytest
from unittest.mock import patch, call


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
