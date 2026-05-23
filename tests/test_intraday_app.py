# tests/test_intraday_app.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch


@pytest.fixture
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _mock_evals():
    return [{"pair": "BTC", "direction": "LONG", "confidence": "high", "aligned": True,
             "squeeze_warning": None, "reasoning": "r", "bias_summary": "b",
             "structure_summary": "s", "entry_zone_summary": "e",
             "funding_summary": "f", "oi_summary": "o", "squeeze_summary": "sq",
             "dominance_summary": "d"}]


def _mock_ranked():
    return [{"rank": 1, "pair": "BTC", "direction": "LONG", "confidence": "high",
             "aligned": True, "squeeze_warning": None, "summary": "ok"}]


def test_evaluations_passes_mode_intraday_to_batch(client):
    with patch("app.run_agent_batch", return_value=_mock_evals()) as mock_batch, \
         patch("app.run_synthesis", return_value=_mock_ranked()):
        resp = client.post("/evaluations", json={"pairs": ["BTC"], "mode": "intraday"})

    assert resp.status_code == 200
    mock_batch.assert_called_once_with(["BTC"], mode="intraday")


def test_evaluations_defaults_to_swing(client):
    with patch("app.run_agent_batch", return_value=_mock_evals()) as mock_batch, \
         patch("app.run_synthesis", return_value=_mock_ranked()):
        resp = client.post("/evaluations", json={"pairs": ["BTC"]})

    assert resp.status_code == 200
    mock_batch.assert_called_once_with(["BTC"], mode="swing")


def test_evaluations_returns_400_for_unknown_mode(client):
    resp = client.post("/evaluations", json={"pairs": ["BTC"], "mode": "scalp"})
    assert resp.status_code == 400
    assert "mode" in resp.get_json()["error"].lower()


def test_evaluations_passes_mode_to_synthesis(client):
    with patch("app.run_agent_batch", return_value=_mock_evals()), \
         patch("app.run_synthesis", return_value=_mock_ranked()) as mock_synth:
        client.post("/evaluations", json={"pairs": ["BTC"], "mode": "intraday"})

    mock_synth.assert_called_once_with(_mock_evals(), mode="intraday")


def test_evaluations_top_passes_mode_to_batch(client):
    mock_tickers = [{"symbol": "BTC-USDT", "quoteVolume": "1000000"}]

    with patch("app.requests.get") as mock_get, \
         patch("app.run_agent_batch", return_value=_mock_evals()) as mock_batch, \
         patch("app.run_synthesis", return_value=_mock_ranked()):
        ticker_resp = mock_get.return_value
        ticker_resp.raise_for_status = lambda: None
        ticker_resp.json.return_value = {"data": mock_tickers}

        resp = client.post("/evaluations/top", json={"top": 1, "mode": "intraday"})

    assert resp.status_code == 200
    _, kwargs = mock_batch.call_args
    assert kwargs.get("mode") == "intraday"


def test_evaluations_top_returns_400_for_unknown_mode(client):
    resp = client.post("/evaluations/top", json={"mode": "scalp"})
    assert resp.status_code == 400
