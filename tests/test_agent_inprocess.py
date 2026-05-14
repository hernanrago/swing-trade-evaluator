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


# ── Task 7: execute_skill in-process ──────────────────────────────────────

def test_execute_skill_calls_function_not_subprocess():
    with patch("agent._skill_fn") as mock_fn_lookup, \
         patch("agent.subprocess.run") as mock_sub:
        mock_skill = MagicMock(return_value={"direction": "LONG"})
        mock_fn_lookup.return_value = mock_skill
        from agent import execute_skill
        result = execute_skill("evaluate_btc_dominance", {"pair": "BTC"})
    mock_sub.assert_not_called()
    mock_skill.assert_called_once_with("BTC", context=None)
    assert result == {"direction": "LONG"}

def test_execute_skill_falls_back_to_subprocess_when_not_in_map():
    completed = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
    with patch("agent._skill_fn", return_value=None), \
         patch("agent.subprocess.run", return_value=completed) as mock_sub:
        from agent import execute_skill
        execute_skill("evaluate_btc_dominance", {"pair": "BTC"})
    mock_sub.assert_called_once()

def test_execute_skill_passes_context_to_function():
    ctx = {"btc_dominance": 55.0}
    with patch("agent._skill_fn") as mock_fn_lookup, \
         patch("agent.subprocess.run"):
        mock_skill = MagicMock(return_value={"direction": "LONG"})
        mock_fn_lookup.return_value = mock_skill
        from agent import execute_skill
        execute_skill("evaluate_btc_dominance", {"pair": "BTC"}, context=ctx)
    mock_skill.assert_called_once_with("BTC", context=ctx)


# ── Task 8: context wiring ─────────────────────────────────────────────────

def test_run_agent_batch_builds_context_once_for_n_pairs():
    """_build_batch_context called once regardless of pair count."""
    mock_rec = {"direction": "LONG", "confidence": "high", "aligned": True,
                "squeeze_warning": None, "reasoning": "", "trend_summary": "",
                "structure_summary": "", "dominance_summary": "", "funding_summary": "",
                "oi_summary": "", "squeeze_summary": "", "entry_zone_summary": ""}
    with patch("agent._build_batch_context", return_value={}) as mock_ctx, \
         patch("agent.run_agent", return_value=mock_rec):
        from agent import run_agent_batch
        run_agent_batch(["BTC", "ETH", "SOL"])
    mock_ctx.assert_called_once_with(["BTC", "ETH", "SOL"])

def test_run_agent_receives_context():
    """run_agent must accept context=None without TypeError."""
    stop_response = MagicMock()
    stop_response.choices[0].finish_reason = "stop"
    stop_response.choices[0].message.content = '{"direction":"LONG","confidence":"high","aligned":true,"squeeze_warning":null,"reasoning":"r","trend_summary":"t","structure_summary":"s","dominance_summary":"d","funding_summary":"f","oi_summary":"o","squeeze_summary":"sq","entry_zone_summary":"ez"}'
    stop_response.choices[0].message.tool_calls = None

    with patch("agent.litellm.completion", return_value=stop_response):
        from agent import run_agent
        ctx = {"btc_dominance": 52.0, "premium_index": {}, "spot_prices": {}}
        result = run_agent("BTC", context=ctx)
    assert "direction" in result
