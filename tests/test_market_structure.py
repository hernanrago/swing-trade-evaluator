# tests/test_market_structure.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))

import pytest
from evaluate_market_structure import _normalize_pair

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
