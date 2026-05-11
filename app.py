#!/usr/bin/env python3
"""
API Server for Swing Trade Evaluator
Exposes evaluation endpoint via HTTP.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)

def run_skill(pair):
    """Runs the evaluate-tf-trend skill."""
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
    Output: Trend analysis with direction recommendation
    """
    try:
        data = request.get_json()
        
        if not data or "pair" not in data:
            return jsonify({"error": "Missing 'pair' parameter"}), 400
        
        pair = data.get("pair")
        
        # Call skill
        skill_result = run_skill(pair)
        
        if "error" in skill_result:
            return jsonify(skill_result), 500
        
        # Return skill result
        return jsonify(skill_result)
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"""
    ╔═══════════════════════════════════════════════════════════╗
    ║   Swing Trade Evaluator API Server                        ║
    ║                                                           ║
    ║   Running on: http://0.0.0.0:{port}                      ║
    ║   Health: http://0.0.0.0:{port}/health                   ║
    ║   Evaluate: POST http://0.0.0.0:{port}/evaluate          ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    app.run(host="0.0.0.0", port=port, debug=False)
    
