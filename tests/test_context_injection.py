import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))

import pytest
from unittest.mock import patch, MagicMock


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


# ── Task 2: evaluate_btc_dominance ─────────────────────────────────────────

def test_btc_dominance_uses_context_skips_http():
    from evaluate_btc_dominance import evaluate_btc_dominance
    ctx = {"btc_dominance": 55.5}
    with patch("evaluate_btc_dominance.requests.get") as mock_get:
        result = evaluate_btc_dominance("BTC", context=ctx)
    mock_get.assert_not_called()
    assert result["btc_dominance"] == 55.5

def test_btc_dominance_fetches_when_no_context():
    from evaluate_btc_dominance import evaluate_btc_dominance
    mock_resp = MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": {"data": {"market_cap_percentage": {"btc": 52.0}}},
    })
    with patch("evaluate_btc_dominance.requests.get", return_value=mock_resp) as mock_get:
        result = evaluate_btc_dominance("BTC", context=None)
    mock_get.assert_called_once()
    assert result["btc_dominance"] == 52.0


# ── Task 3: evaluate_funding_rate ─────────────────────────────────────────

def test_funding_rate_uses_context_skips_http():
    from evaluate_funding_rate import evaluate_funding_rate
    ctx = {"premium_index": {"BTC-USDT": {"lastFundingRate": 0.00015, "markPrice": 103500.0}}}
    with patch("evaluate_funding_rate.requests.get") as mock_get:
        result = evaluate_funding_rate("BTC", context=ctx)
    mock_get.assert_not_called()
    assert result["funding_rate"] == 0.00015

def test_funding_rate_fetches_when_no_context():
    from evaluate_funding_rate import evaluate_funding_rate
    mock_resp = MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": {"data": {"lastFundingRate": "0.0001"}},
    })
    with patch("evaluate_funding_rate.requests.get", return_value=mock_resp) as mock_get:
        result = evaluate_funding_rate("BTC", context=None)
    mock_get.assert_called_once()
    assert result["funding_rate"] == 0.0001


# ── Task 4: evaluate_squeeze_risk ─────────────────────────────────────────

def _gateio_stats_response():
    return MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": [
            {"lsr_account": "1.2", "open_interest_usd": "1000000", "long_liq_usd": "500", "short_liq_usd": "500"},
            {"lsr_account": "1.2", "open_interest_usd": "1050000", "long_liq_usd": "600", "short_liq_usd": "400"},
        ],
    })

def test_squeeze_risk_uses_context_skips_bingx_and_coingecko():
    from evaluate_squeeze_risk import evaluate_squeeze_risk
    ctx = {
        "premium_index": {"BTC-USDT": {"lastFundingRate": 0.0001, "markPrice": 103500.0}},
        "spot_prices": {"BTC": 103480.0},
    }
    with patch("evaluate_squeeze_risk.requests.get", return_value=_gateio_stats_response()) as mock_get:
        result = evaluate_squeeze_risk("BTC", context=ctx)
    # Only Gate.io should have been called (1 call), not BingX or CoinGecko
    assert mock_get.call_count == 1
    call_url = mock_get.call_args[0][0]
    assert "gateio" in call_url

def test_squeeze_risk_fetches_all_when_no_context():
    from evaluate_squeeze_risk import evaluate_squeeze_risk
    bingx_resp = MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": {"data": {"lastFundingRate": "0.0001", "markPrice": "103500"}},
    })
    coingecko_resp = MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": {"bitcoin": {"usd": 103480.0}},
    })
    def side_effect(url, **kwargs):
        if "bingx" in url:
            return bingx_resp
        if "coingecko" in url:
            return coingecko_resp
        return _gateio_stats_response()
    with patch("evaluate_squeeze_risk.requests.get", side_effect=side_effect) as mock_get:
        evaluate_squeeze_risk("BTC", context=None)
    assert mock_get.call_count == 3


# ── Task 5: consistent context=None signatures ─────────────────────────────

def test_skills_accept_context_none():
    """All skills must accept context=None without raising TypeError."""
    import inspect, importlib
    for mod_name, fn_name in [
        ("evaluate_open_interest",    "evaluate_open_interest"),
        ("evaluate_tf_trend",         "evaluate_tf_trend"),
        ("evaluate_market_structure", "evaluate_market_structure"),
    ]:
        mod = importlib.import_module(mod_name)
        fn  = getattr(mod, fn_name)
        sig = inspect.signature(fn)
        assert "context" in sig.parameters, f"{fn_name} missing context param"
        assert sig.parameters["context"].default is None, f"{fn_name} context default is not None"

def test_entry_zone_passes_context_to_market_structure():
    """evaluate_entry_zone must forward context to evaluate_market_structure."""
    import evaluate_entry_zone as ez

    ms_result = {
        "conclusion": "LONG", "4H": {"structure": "LONG"}, "1D": {"structure": "LONG"},
        "invalidation_level": 95000.0, "range_high": 110000.0, "range_low": 95000.0,
    }
    candles = [[str(i * 1000), "100", "110", "90", "105", "1000", "1"] for i in range(1, 230)]

    with patch("evaluate_entry_zone.evaluate_market_structure") as mock_ms, \
         patch("evaluate_entry_zone._get_candles", return_value=candles):
        mock_ms.return_value = ms_result
        ctx = {"btc_dominance": 52.0}
        ez.evaluate_entry_zone("BTC", context=ctx)
        _, kwargs = mock_ms.call_args
        assert kwargs.get("context") == ctx
