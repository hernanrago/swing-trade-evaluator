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
