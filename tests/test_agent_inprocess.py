# tests/test_agent_inprocess.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"))

import pytest
from unittest.mock import patch, MagicMock, call


# ── Task 6: timed_cache ────────────────────────────────────────────────────

def test_timed_cache_returns_cached_value():
    from agent import timed_cache
    call_count = 0
    @timed_cache(seconds=60)
    def expensive():
        nonlocal call_count
        call_count += 1
        return 42
    assert expensive() == 42
    assert expensive() == 42
    assert call_count == 1   # called only once

def test_timed_cache_refreshes_after_ttl():
    import datetime
    from agent import timed_cache
    call_count = 0
    @timed_cache(seconds=1)
    def expensive():
        nonlocal call_count
        call_count += 1
        return call_count
    expensive()
    # force TTL expiry by advancing datetime.now inside agent module
    future = datetime.datetime.now() + datetime.timedelta(seconds=2)
    with patch("agent.datetime") as mock_dt:
        mock_dt.now.return_value = future
        mock_dt.timedelta = datetime.timedelta
        expensive()
    assert call_count == 2


# ── Task 6: _build_batch_context ──────────────────────────────────────────

def test_build_batch_context_returns_dict_with_expected_keys():
    with patch("agent._cached_btc_dominance", return_value=52.0), \
         patch("agent._cached_premium_index", return_value={"BTC-USDT": {}}), \
         patch("agent._fetch_spot_prices_batch", return_value={"BTC": 103000.0}):
        from agent import _build_batch_context
        ctx = _build_batch_context(["BTC"])
    assert ctx["btc_dominance"] == 52.0
    assert "BTC-USDT" in ctx["premium_index"]
    assert ctx["spot_prices"]["BTC"] == 103000.0

def test_build_batch_context_tolerates_fetch_error():
    """A failed pre-fetch must not crash the batch — key is absent, not raised."""
    with patch("agent._cached_btc_dominance", side_effect=Exception("network error")), \
         patch("agent._cached_premium_index", return_value={}), \
         patch("agent._fetch_spot_prices_batch", return_value={}):
        from agent import _build_batch_context
        ctx = _build_batch_context(["BTC"])
    assert "btc_dominance" not in ctx
