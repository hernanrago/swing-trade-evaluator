#!/usr/bin/env python3
"""
CLI entry point for the Swing Trade Evaluator.
Agent logic lives in agent.py.
"""

import json
import argparse
from dotenv import load_dotenv
load_dotenv()
from agent import run_agent

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate crypto swing trade direction")
    parser.add_argument("pair", help="Cryptocurrency pair (e.g., BTC, ETH, SOL)")
    args = parser.parse_args()

    result = run_agent(args.pair.upper())

    print("\n" + "=" * 70)
    print("SWING TRADE EVALUATION")
    print("=" * 70)
    print(json.dumps(result, indent=2))
