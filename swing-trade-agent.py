#!/usr/bin/env python3
"""
Agent: Swing Trade Direction Recommender (v2)
Evaluates trend + BTC dominance impact.
Input: pair (e.g., BTC)
"""

import json
import sys
import subprocess
from anthropic import Anthropic

client = Anthropic()

# Tools available to the agent
TOOLS = [
    {
        "name": "evaluate_tf_trend",
        "description": "Analyzes 1D/1W trend and recommends LONG or SHORT direction for a given pair",
        "input_schema": {
            "type": "object",
            "properties": {
                "pair": {
                    "type": "string",
                    "description": "Cryptocurrency pair, e.g. BTC, ETH, SOL (without USDT suffix)"
                }
            },
            "required": ["pair"]
        }
    },
    {
        "name": "evaluate_btc_dominance",
        "description": "Analyzes BTC dominance and its impact on the pair (bullish for BTC when dominance rises, bearish for altcoins)",
        "input_schema": {
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
]

def call_skill(skill_name, params):
    """Executes a skill via subprocess."""
    if skill_name == "evaluate_tf_trend":
        pair = params.get("pair", "BTC")
        
        try:
            result = subprocess.run(
                [
                    "python3",
                    "./evaluate-tf-trend.py",
                    "--pair", pair
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                return {"error": f"Skill error: {result.stderr}"}
            
            return json.loads(result.stdout)
        
        except json.JSONDecodeError:
            return {"error": "Invalid JSON from skill"}
        except Exception as e:
            return {"error": str(e)}
    
    elif skill_name == "evaluate_btc_dominance":
        pair = params.get("pair", "BTC")
        
        try:
            result = subprocess.run(
                [
                    "python3",
                    "./evaluate-btc-dominance.py",
                    "--pair", pair
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                return {"error": f"Skill error: {result.stderr}"}
            
            return json.loads(result.stdout)
        
        except json.JSONDecodeError:
            return {"error": "Invalid JSON from skill"}
        except Exception as e:
            return {"error": str(e)}
    
    return {"error": f"Skill '{skill_name}' not found"}

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

    user_message = f"""
Evaluate {pair} considering:
1. 1D/1W trend direction
2. BTC dominance impact

Provide a final recommendation: LONG or SHORT
"""

    messages = [
        {"role": "user", "content": user_message}
    ]
    
    print(f"\n[*] Analyzing {pair} (trend + dominance)...", file=sys.stderr)
    
    # Agentic loop
    iteration = 0
    max_iterations = 10
    
    while iteration < max_iterations:
        iteration += 1
        
        print(f"[*] Iteration {iteration}...", file=sys.stderr)
        
        response = client.messages.create(
            model="claude-opus-4-20250805",
            max_tokens=2048,
            system=system_prompt,
            tools=TOOLS,
            messages=messages
        )
        
        # Check stop reason
        if response.stop_reason == "end_turn":
            print(f"[✓] Agent completed analysis", file=sys.stderr)
            
            # Extract final text response
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            
            return "No response generated"
        
        elif response.stop_reason == "tool_use":
            print(f"[*] Agent called tool_use", file=sys.stderr)
            
            # Add assistant response
            messages.append({"role": "assistant", "content": response.content})
            
            # Process tool_use blocks
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    
                    print(f"[*] Calling skill: {tool_name}", file=sys.stderr)
                    print(f"    Parameters: {json.dumps(tool_input)}", file=sys.stderr)
                    
                    # Execute the skill
                    result = call_skill(tool_name, tool_input)
                    
                    print(f"[✓] Result:", file=sys.stderr)
                    print(f"    {json.dumps(result, indent=2)}", file=sys.stderr)
                    
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })
            
            # Add tool results
            messages.append({"role": "user", "content": tool_results})
        
        else:
            print(f"[!] Unknown stop reason: {response.stop_reason}", file=sys.stderr)
            break
    
    return f"Agent did not converge after {max_iterations} iterations"

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