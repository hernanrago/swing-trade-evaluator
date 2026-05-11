#!/usr/bin/env python3
"""
Agent: Swing Trade Direction Recommender (v3 - Multi-provider)
Evaluates trend + BTC dominance impact via LiteLLM (supports Anthropic, OpenAI, Gemini, etc.)
Input: pair (e.g., BTC)
"""

import os
import json
import sys
import subprocess
import litellm

# --- Config (override via environment variables) ---
LLM_MODEL            = os.environ.get("LLM_MODEL",            "claude-opus-4-7")
MAX_TOKENS           = int(os.environ.get("MAX_TOKENS",          "4096"))
MAX_AGENT_ITERATIONS = int(os.environ.get("MAX_AGENT_ITERATIONS", "10"))
SUBPROCESS_TIMEOUT   = int(os.environ.get("SUBPROCESS_TIMEOUT",   "30"))

# Tools available to the agent
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "evaluate_tf_trend",
            "description": "Analyzes 1D/1W trend and recommends LONG or SHORT direction for a given pair",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {
                        "type": "string",
                        "description": "Cryptocurrency pair, e.g. BTC, ETH, SOL (without USDT suffix)"
                    }
                },
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_btc_dominance",
            "description": "Analyzes BTC dominance and its impact on the pair (bullish for BTC when dominance rises, bearish for altcoins)",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {
                        "type": "string",
                        "description": "Cryptocurrency pair, e.g. BTC, ETH, SOL"
                    }
                },
                "required": ["pair"]
            }
        }
    }
]

def call_skill(skill_name, params):
    """Executes a skill via subprocess."""
    script_map = {
        "evaluate_tf_trend": "./evaluate-tf-trend.py",
        "evaluate_btc_dominance": "./evaluate-btc-dominance.py",
    }

    script = script_map.get(skill_name)
    if not script:
        return {"error": f"Skill '{skill_name}' not found"}

    pair = params.get("pair", "BTC")

    try:
        result = subprocess.run(
            ["python3", script, "--pair", pair],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT
        )

        if result.returncode != 0:
            return {"error": f"Skill error: {result.stderr}"}

        return json.loads(result.stdout)

    except json.JSONDecodeError:
        return {"error": "Invalid JSON from skill"}
    except Exception as e:
        return {"error": str(e)}

def run_agent(pair):
    """
    Runs the agent to evaluate trend + dominance impact.
    Input: pair (e.g., BTC)
    """

    system_prompt = """You are a crypto trend analysis agent. Your job is to:

1. Call evaluate_tf_trend to get 1D/1W trend analysis
2. Call evaluate_btc_dominance to understand BTC dominance impact
3. Combine both analyses into a cohesive recommendation

Analyze the results:
- Do the trend and dominance indicators align or conflict?
- What is your overall direction recommendation (LONG or SHORT)?
- How confident are you?

Keep your response concise and focused on the trend analysis and direction recommendation."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Evaluate {pair} considering 1D/1W trend direction and BTC dominance impact. Provide a final recommendation: LONG or SHORT"}
    ]

    print(f"\n[*] Analyzing {pair} (trend + dominance) with {LLM_MODEL}...", file=sys.stderr)

    for iteration in range(1, MAX_AGENT_ITERATIONS + 1):
        print(f"[*] Iteration {iteration}...", file=sys.stderr)

        response = litellm.completion(
            model=LLM_MODEL,
            max_tokens=MAX_TOKENS,
            messages=messages,
            tools=TOOLS,
        )

        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        if finish_reason == "stop":
            print(f"[✓] Agent completed analysis", file=sys.stderr)
            return message.content or "No response generated"

        elif finish_reason == "tool_calls":
            print(f"[*] Agent called tools", file=sys.stderr)
            messages.append(message)

            for tc in message.tool_calls:
                tool_input = json.loads(tc.function.arguments)
                print(f"[*] Calling skill: {tc.function.name}", file=sys.stderr)
                print(f"    Parameters: {json.dumps(tool_input)}", file=sys.stderr)

                result = call_skill(tc.function.name, tool_input)

                print(f"[✓] Result:", file=sys.stderr)
                print(f"    {json.dumps(result, indent=2)}", file=sys.stderr)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result)
                })

        else:
            print(f"[!] Unknown finish reason: {finish_reason}", file=sys.stderr)
            break

    return f"Agent did not converge after {MAX_AGENT_ITERATIONS} iterations"

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate crypto trend + BTC dominance")
    parser.add_argument("pair", help="Cryptocurrency pair (e.g., BTC, ETH, SOL)")

    args = parser.parse_args()

    result = run_agent(args.pair)

    print("\n" + "="*70)
    print("TREND ANALYSIS + BTC DOMINANCE IMPACT")
    print("="*70)
    print(result)
