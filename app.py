#!/usr/bin/env python3
"""
API Server for Swing Trade Evaluator (v2)
Exposes evaluation endpoint with trend + BTC dominance analysis.
"""

import os
import json
import subprocess
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

def run_skill(skill_name, pair):
    """Runs a skill via subprocess."""
    try:
        if skill_name == "trend":
            result = subprocess.run(
                ["python3", "./evaluate-tf-trend.py", "--pair", pair],
                capture_output=True,
                text=True,
                timeout=30
            )
        elif skill_name == "dominance":
            result = subprocess.run(
                ["python3", "./evaluate-btc-dominance.py", "--pair", pair],
                capture_output=True,
                text=True,
                timeout=30
            )
        else:
            return {"error": f"Unknown skill: {skill_name}"}
        
        if result.returncode != 0:
            return {"error": f"Skill error: {result.stderr}"}
        
        return json.loads(result.stdout)
    
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON from skill: {e}"}
    except Exception as e:
        return {"error": str(e)}

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "swing-trade-evaluator-api"
    })

@app.route("/evaluate", methods=["POST"])
def evaluate():
    """
    Main evaluation endpoint.
    Input: { "pair": "BTC" }
    Output: Trend analysis + BTC dominance impact
    """
    try:
        data = request.get_json()
        
        if not data or "pair" not in data:
            return jsonify({"error": "Missing 'pair' parameter"}), 400
        
        pair = data.get("pair").upper()
        
        # Call both skills
        trend_result = run_skill("trend", pair)
        dominance_result = run_skill("dominance", pair)
        
        if "error" in trend_result:
            return jsonify(trend_result), 500
        
        if "error" in dominance_result:
            return jsonify(dominance_result), 500
        
        # Combine results
        combined = {
            "timestamp": datetime.now().isoformat(),
            "pair": pair,
            "trend_analysis": trend_result,
            "dominance_analysis": dominance_result,
            "combined_recommendation": combine_recommendations(trend_result, dominance_result)
        }
        
        return jsonify(combined)
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def combine_recommendations(trend, dominance):
    """Combines trend and dominance recommendations."""
    trend_long = trend.get("recommended_direction") == "LONG"
    dominance_long = dominance.get("recommended_direction") == "LONG"
    
    # Both agree
    if trend_long == dominance_long:
        confidence = "high"
        direction = "LONG" if trend_long else "SHORT"
    # They disagree
    else:
        confidence = "moderate"
        direction = trend["recommended_direction"]  # Trend is primary, dominance is secondary
    
    return {
        "direction": direction,
        "confidence": confidence,
        "aligned": trend_long == dominance_long,
        "reasoning": f"Trend recommends {trend['recommended_direction']}, Dominance {'supports' if dominance_long else 'opposes'} LONG"
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"""
    ╔═══════════════════════════════════════════════════════════╗
    ║   Swing Trade Evaluator API Server (v2)                   ║
    ║                                                           ║
    ║   Running on: http://0.0.0.0:{port}                      ║
    ║   Health: http://0.0.0.0:{port}/health                   ║
    ║   Evaluate: POST http://0.0.0.0:{port}/evaluate          ║
    ║                                                           ║
    ║   Skills: evaluate-tf-trend, evaluate-btc-dominance      ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    app.run(host="0.0.0.0", port=port, debug=False)