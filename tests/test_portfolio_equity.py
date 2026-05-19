# tests/test_portfolio_equity.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import time
from unittest.mock import patch


def _rec(t_ms, symbol, income):
    return {"time": t_ms, "symbol": symbol, "income": str(income)}


# ── _fetch_realized_pnl ──────────────────────────────────────────────────────

def test_fetch_single_page():
    from app import _fetch_realized_pnl
    now_ms = int(time.time() * 1000)
    records = [_rec(now_ms - 1000, "BTC-USDT", "10.0")]
    with patch("app._bingx_signed_get", return_value={"code": 0, "data": records}):
        result = _fetch_realized_pnl(30)
    assert result == records


def test_fetch_paginates_on_1000_records():
    from app import _fetch_realized_pnl
    now_ms = int(time.time() * 1000)
    page1 = [_rec(now_ms - i * 1000, "BTC-USDT", "1.0") for i in range(1, 1001)]
    page2 = [_rec(now_ms - 1_001_000, "ETH-USDT", "2.0")]
    with patch("app._bingx_signed_get", side_effect=[
        {"code": 0, "data": page1},
        {"code": 0, "data": page2},
    ]):
        result = _fetch_realized_pnl(30)
    assert len(result) == 1001


def test_fetch_raises_on_bingx_error():
    from app import _fetch_realized_pnl
    with patch("app._bingx_signed_get", return_value={"code": -1, "msg": "invalid key"}):
        with pytest.raises(ValueError, match="BingX error"):
            _fetch_realized_pnl(30)


# ── GET /portfolio/equity ────────────────────────────────────────────────────

@pytest.fixture
def client():
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_equity_happy_path(client):
    now_ms = int(time.time() * 1000)
    records = [
        _rec(now_ms - 2000, "BTC-USDT", "50.0"),
        _rec(now_ms - 1000, "ETH-USDT", "-10.0"),
    ]
    with patch("app.BINGX_API_KEY", "k"), \
         patch("app.BINGX_API_SECRET", "s"), \
         patch("app._fetch_realized_pnl", return_value=records):
        resp = client.get("/portfolio/equity?days=30")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["period_days"] == 30
    assert len(data["equity_curve"]) == 2
    assert data["equity_curve"][0]["pnl"] == 50.0
    assert data["equity_curve"][0]["cumulative_pnl"] == 50.0
    assert data["equity_curve"][1]["pnl"] == -10.0
    assert data["equity_curve"][1]["cumulative_pnl"] == 40.0
    assert data["summary"]["total_pnl"] == 40.0
    assert data["summary"]["trade_count"] == 2
    assert data["summary"]["win_rate"] == 0.5
    assert data["summary"]["best_trade"] == 50.0
    assert data["summary"]["worst_trade"] == -10.0


def test_equity_default_days(client):
    with patch("app.BINGX_API_KEY", "k"), \
         patch("app.BINGX_API_SECRET", "s"), \
         patch("app._fetch_realized_pnl", return_value=[]) as mock_fetch:
        client.get("/portfolio/equity")
    mock_fetch.assert_called_once_with(30)


def test_equity_days_too_low(client):
    with patch("app.BINGX_API_KEY", "k"), patch("app.BINGX_API_SECRET", "s"):
        assert client.get("/portfolio/equity?days=0").status_code == 400


def test_equity_days_too_high(client):
    with patch("app.BINGX_API_KEY", "k"), patch("app.BINGX_API_SECRET", "s"):
        assert client.get("/portfolio/equity?days=91").status_code == 400


def test_equity_missing_credentials(client):
    with patch("app.BINGX_API_KEY", ""), patch("app.BINGX_API_SECRET", ""):
        assert client.get("/portfolio/equity").status_code == 503


def test_equity_bingx_502(client):
    with patch("app.BINGX_API_KEY", "k"), \
         patch("app.BINGX_API_SECRET", "s"), \
         patch("app._fetch_realized_pnl", side_effect=ValueError("BingX error: invalid key")):
        assert client.get("/portfolio/equity").status_code == 502


def test_equity_empty_result(client):
    with patch("app.BINGX_API_KEY", "k"), \
         patch("app.BINGX_API_SECRET", "s"), \
         patch("app._fetch_realized_pnl", return_value=[]):
        resp = client.get("/portfolio/equity")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["equity_curve"] == []
    assert data["summary"]["trade_count"] == 0
    assert data["summary"]["win_rate"] == 0.0
    assert data["summary"]["best_trade"] == 0.0
    assert data["summary"]["worst_trade"] == 0.0
