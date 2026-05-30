#!/usr/bin/env python3
"""
CLI entry point for the Swing/Intraday Trade Evaluator.
Agent logic lives in agent.py.

Env vars (email is skipped if not set):
    EMAIL_TO        Recipient address
    SMTP_USER       Gmail address used to send
    SMTP_PASSWORD   Gmail App Password (16-char, no spaces)
    RESEND_API_KEY  Resend API key (takes priority over SMTP)
    EMAIL_FROM      Sender address for Resend (default: onboarding@resend.dev)
"""

import json
import os
import smtplib
import argparse
import requests
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
load_dotenv()
from agent import run_agent


def _direction_color(direction: str) -> str:
    return {"LONG": "#15803d", "SHORT": "#dc2626"}.get(direction, "#334155")


def _confidence_color(confidence: str) -> str:
    return {"high": "#15803d", "moderate": "#d97706", "low": "#dc2626"}.get(confidence, "#334155")


def build_html(pair: str, mode: str, result: dict, timestamp: str) -> str:
    direction   = result.get("direction", "N/A")
    confidence  = result.get("confidence", "N/A")
    aligned     = result.get("aligned")
    squeeze     = result.get("squeeze_warning")
    reasoning   = result.get("reasoning", "")

    summaries = [
        ("Tendencia",       result.get("trend_summary")),
        ("Estructura",      result.get("structure_summary")),
        ("Dominancia BTC",  result.get("dominance_summary")),
        ("Funding",         result.get("funding_summary")),
        ("Open Interest",   result.get("oi_summary")),
        ("Squeeze Risk",    result.get("squeeze_summary")),
        ("Entry Zone",      result.get("entry_zone_summary")),
    ]

    aligned_label = "Alineado ✅" if aligned else "Conflicto ⚠️"
    aligned_color = "#15803d" if aligned else "#d97706"

    squeeze_html = ""
    if squeeze:
        squeeze_html = (
            f'<p style="margin:16px 0 0;padding:10px 14px;background:#fef2f2;'
            f'border-left:4px solid #dc2626;color:#dc2626;font-size:13px;">'
            f'⚠️ Squeeze Warning: {squeeze}</p>'
        )

    rows_html = ""
    for i, (label, value) in enumerate(summaries):
        if not value:
            continue
        bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
        rows_html += (
            f'<tr style="background:{bg};">'
            f'<td style="padding:8px 14px;color:#64748b;font-size:13px;white-space:nowrap;">{label}</td>'
            f'<td style="padding:8px 14px;font-size:13px;color:#334155;">{value}</td>'
            f'</tr>'
        )

    mode_label = mode.upper()

    return f"""
    <div style="font-family:sans-serif;max-width:720px;margin:auto;padding:24px;background:#ffffff;">
      <h2 style="margin:0 0 4px;color:#0f172a;">Swing Trade Evaluator — {pair}</h2>
      <p style="margin:0 0 20px;color:#64748b;font-size:13px;">{timestamp} · {mode_label}</p>

      <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
        <tr style="background:#1e293b;color:#f1f5f9;">
          <th style="padding:10px 14px;text-align:left;font-weight:normal;color:#94a3b8;font-size:12px;">Dirección</th>
          <th style="padding:10px 14px;text-align:left;font-weight:normal;color:#94a3b8;font-size:12px;">Confianza</th>
          <th style="padding:10px 14px;text-align:left;font-weight:normal;color:#94a3b8;font-size:12px;">Señal</th>
        </tr>
        <tr>
          <td style="padding:12px 14px;font-size:20px;font-weight:bold;color:{_direction_color(direction)};">{direction}</td>
          <td style="padding:12px 14px;font-size:16px;font-weight:bold;color:{_confidence_color(confidence)};">{confidence}</td>
          <td style="padding:12px 14px;font-size:14px;font-weight:bold;color:{aligned_color};">{aligned_label}</td>
        </tr>
      </table>

      {squeeze_html}

      <p style="margin:20px 0 12px;color:#334155;font-size:13px;line-height:1.6;">{reasoning}</p>

      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#1e293b;color:#94a3b8;">
            <th style="padding:8px 14px;text-align:left;font-weight:normal;width:130px;">Skill</th>
            <th style="padding:8px 14px;text-align:left;font-weight:normal;">Resumen</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>

      <p style="margin-top:24px;color:#94a3b8;font-size:11px;text-align:center;">
        swing-trade-evaluator · Railway cron
      </p>
    </div>"""


def send_email_smtp(subject: str, html: str) -> None:
    email_to  = os.environ["EMAIL_TO"]
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASSWORD"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = email_to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(smtp_user, smtp_pass)
        smtp.sendmail(smtp_user, email_to, msg.as_string())

    print(f"Email enviado → {email_to}")


def send_email_resend(subject: str, html: str) -> None:
    api_key    = os.environ["RESEND_API_KEY"]
    email_to   = os.environ["EMAIL_TO"]
    email_from = os.environ.get("EMAIL_FROM", "Swing Trade <onboarding@resend.dev>")

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": email_from,
            "to": [email_to],
            "subject": subject,
            "html": html,
        },
        timeout=20,
    )
    response.raise_for_status()
    print(f"Email enviado via Resend → {email_to}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate crypto trade direction")
    parser.add_argument("pair", help="Cryptocurrency pair (e.g., BTC, ETH, SOL)")
    parser.add_argument(
        "--mode",
        choices=["swing", "intraday"],
        default="swing",
        help="Evaluation mode: swing (default) or intraday",
    )
    args = parser.parse_args()

    pair = args.pair.upper()
    result = run_agent(pair, mode=args.mode)

    header = "INTRADAY TRADE EVALUATION" if args.mode == "intraday" else "SWING TRADE EVALUATION"
    print("\n" + "=" * 70)
    print(header)
    print("=" * 70)
    print(json.dumps(result, indent=2))

    if not os.environ.get("RESEND_API_KEY") and not os.environ.get("SMTP_USER"):
        print("Ni RESEND_API_KEY ni SMTP_USER configurados — email omitido.")
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        direction  = result.get("direction", "N/A")
        confidence = result.get("confidence", "")
        squeeze    = result.get("squeeze_warning")

        if squeeze:
            subject = f"⚠️ {pair} {direction} — Squeeze Warning · {timestamp}"
        elif confidence == "high":
            subject = f"📊 {pair} {direction} high confidence · {timestamp}"
        else:
            subject = f"📊 {pair} {direction} {confidence} · {timestamp}"

        html = build_html(pair, args.mode, result, timestamp)

        try:
            if os.environ.get("RESEND_API_KEY"):
                send_email_resend(subject, html)
            else:
                send_email_smtp(subject, html)
        except Exception as exc:
            print(f"Email no enviado: {exc}")
