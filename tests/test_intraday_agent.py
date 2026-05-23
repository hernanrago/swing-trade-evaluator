# tests/test_intraday_agent.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock


def test_tools_is_dict_with_swing_and_intraday():
    from agent import TOOLS
    assert isinstance(TOOLS, dict)
    assert "swing" in TOOLS
    assert "intraday" in TOOLS


def test_swing_tools_has_seven_entries():
    from agent import TOOLS
    assert len(TOOLS["swing"]) == 7


def test_intraday_tools_has_seven_entries():
    from agent import TOOLS
    assert len(TOOLS["intraday"]) == 7


def test_system_prompt_is_dict_with_swing_and_intraday():
    from agent import SYSTEM_PROMPT
    assert isinstance(SYSTEM_PROMPT, dict)
    assert "swing" in SYSTEM_PROMPT
    assert "intraday" in SYSTEM_PROMPT


def test_intraday_system_prompt_contains_bias_summary():
    from agent import SYSTEM_PROMPT
    assert "bias_summary" in SYSTEM_PROMPT["intraday"]


def test_swing_system_prompt_contains_trend_summary():
    from agent import SYSTEM_PROMPT
    assert "trend_summary" in SYSTEM_PROMPT["swing"]


def test_multi_system_prompt_is_dict():
    from agent import _MULTI_SYSTEM_PROMPT
    assert isinstance(_MULTI_SYSTEM_PROMPT, dict)
    assert "swing" in _MULTI_SYSTEM_PROMPT
    assert "intraday" in _MULTI_SYSTEM_PROMPT


def test_valid_modes_constant_exists():
    from agent import VALID_MODES
    assert "swing" in VALID_MODES
    assert "intraday" in VALID_MODES


def test_run_agent_invalid_mode_raises_value_error():
    from agent import run_agent
    with pytest.raises(ValueError, match="Unknown mode"):
        run_agent("BTC", mode="scalp")


def test_run_agent_uses_intraday_tools_when_mode_intraday():
    """litellm.completion must receive intraday tools, not swing tools."""
    stop_resp = MagicMock()
    stop_resp.choices[0].finish_reason = "stop"
    stop_resp.choices[0].message.content = (
        '{"direction":"LONG","confidence":"high","aligned":true,'
        '"squeeze_warning":null,"reasoning":"r","bias_summary":"b",'
        '"structure_summary":"s","entry_zone_summary":"e","funding_summary":"f",'
        '"oi_summary":"o","squeeze_summary":"sq","dominance_summary":"d"}'
    )
    stop_resp.choices[0].message.tool_calls = None

    with patch("agent.litellm.completion", return_value=stop_resp) as mock_llm:
        from agent import run_agent, TOOLS
        run_agent("BTC", mode="intraday")

    call_kwargs = mock_llm.call_args.kwargs
    assert call_kwargs["tools"] is TOOLS["intraday"]


def test_run_agent_sets_trade_mode_env():
    """os.environ['TRADE_MODE'] must equal mode during execution."""
    observed = {}
    stop_resp = MagicMock()
    stop_resp.choices[0].finish_reason = "stop"
    stop_resp.choices[0].message.content = (
        '{"direction":"LONG","confidence":"high","aligned":true,'
        '"squeeze_warning":null,"reasoning":"r","bias_summary":"b",'
        '"structure_summary":"s","entry_zone_summary":"e","funding_summary":"f",'
        '"oi_summary":"o","squeeze_summary":"sq","dominance_summary":"d"}'
    )
    stop_resp.choices[0].message.tool_calls = None

    def capture_env(*args, **kwargs):
        observed["TRADE_MODE"] = os.environ.get("TRADE_MODE")
        return stop_resp

    with patch("agent.litellm.completion", side_effect=capture_env):
        from agent import run_agent
        run_agent("BTC", mode="intraday")

    assert observed["TRADE_MODE"] == "intraday"


def test_execute_skill_passes_trade_mode_to_subprocess():
    """Subprocess env must include TRADE_MODE=intraday when mode=intraday."""
    completed = MagicMock(returncode=0, stdout='{"ok":true}', stderr="")
    with patch("agent._skill_fn", return_value=None), \
         patch("agent.subprocess.run", return_value=completed) as mock_sub:
        from agent import execute_skill
        execute_skill("evaluate_tf_trend", {"pair": "BTC"}, mode="intraday")

    call_kwargs = mock_sub.call_args.kwargs
    assert call_kwargs["env"]["TRADE_MODE"] == "intraday"


def test_run_agent_batch_propagates_mode():
    """run_agent must be called with the mode passed to run_agent_batch."""
    calls = []

    def fake_agent(pair, mode="swing", context=None):
        calls.append(mode)
        return {"direction": "LONG", "confidence": "high"}

    with patch("agent._build_batch_context", return_value={}), \
         patch("agent.run_agent", side_effect=fake_agent):
        from agent import run_agent_batch
        run_agent_batch(["BTC", "ETH"], mode="intraday")

    assert all(m == "intraday" for m in calls)


def test_run_synthesis_uses_intraday_multi_prompt():
    """litellm.completion must receive the intraday multi-prompt."""
    resp = MagicMock()
    resp.choices[0].message.content = '[{"rank":1,"pair":"BTC","direction":"LONG","confidence":"high","aligned":true,"squeeze_warning":null,"summary":"ok"}]'

    with patch("agent.litellm.completion", return_value=resp) as mock_llm:
        from agent import run_synthesis, _MULTI_SYSTEM_PROMPT
        run_synthesis([{"pair": "BTC", "direction": "LONG"}], mode="intraday")

    system_msg = mock_llm.call_args.kwargs["messages"][0]["content"]
    assert system_msg == _MULTI_SYSTEM_PROMPT["intraday"]
