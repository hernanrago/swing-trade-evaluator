#!/usr/bin/env python3
"""
CLI entry point for the Swing/Intraday Trade Evaluator.
Agent logic lives in agent.py.

Usage:
    python3 cli.py                        # evaluate default 10 pairs
    python3 cli.py BTC                    # evaluate single pair
    python3 cli.py --pairs BTC,ETH,SOL   # evaluate explicit list

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
from agent import run_agent, run_agent_batch, run_synthesis

DEFAULT_PAIRS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "SUI", "ADA", "AVAX", "LINK"]


# ── HTML builders ─────────────────────────────────────────────────────────────

def _direction_color(direction: str) -> str:
    return {"LONG": "#15803d", "SHORT": "#dc2626"}.get(direction, "#334155")


def _confidence_color(confidence: str) -> str:
    return {"high": "#15803d", "moderate": "#d97706", "low": "#dc2626"}.get(confidence, "#334155")


def build_html_single(pair: str, mode: str, result: dict, timestamp: str) -> str:
    direction  = result.get("direction", "N/A")
    confidence = result.get("confidence", "N/A")
    aligned    = result.get("aligned")
    squeeze    = result.get("squeeze_warning")
    reasoning  = result.get("reasoning", "")

    summaries = [
        ("Tendencia",      result.get("trend_summary")),
        ("Estructura",     result.get("structure_summary")),
        ("Dominancia BTC", result.get("dominance_summary")),
        ("Funding",        result.get("funding_summary")),
        ("Open Interest",  result.get("oi_summary")),
        ("Squeeze Risk",   result.get("squeeze_summary")),
        ("Entry Zone",     result.get("entry_zone_summary")),
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

    return f"""
    <div style="font-family:sans-serif;max-width:720px;margin:auto;padding:24px;background:#ffffff;">
      <h2 style="margin:0 0 4px;color:#0f172a;">Swing Trade Evaluator — {pair}</h2>
      <p style="margin:0 0 20px;color:#64748b;font-size:13px;">{timestamp} · {mode.upper()}</p>

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


def build_html_batch(ranked: list, evals: list, mode: str, timestamp: str) -> str:
    eval_map = {e.get("pair", ""): e for e in evals}

    squeeze_pairs = [r["pair"] for r in ranked if r.get("squeeze_warning")]
    high_conf     = [r for r in ranked if r.get("confidence") == "high" and r.get("aligned")]

    summary_line = ""
    if high_conf:
        summary_line = f"<strong>{len(high_conf)} oportunidad(es) high confidence alineada(s)</strong>"
    else:
        summary_line = "Sin oportunidades high confidence alineadas"
    if squeeze_pairs:
        summary_line += f" · ⚠️ Squeeze: {', '.join(squeeze_pairs)}"

    th = "padding:8px 14px;text-align:left;font-weight:bold;color:#94a3b8;font-weight:normal;font-size:12px;"
    td = "padding:8px 14px;font-size:13px;"

    rows_html = ""
    for i, r in enumerate(ranked):
        bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
        pair       = r.get("pair", "")
        direction  = r.get("direction", "")
        confidence = r.get("confidence", "")
        aligned    = r.get("aligned")
        squeeze    = r.get("squeeze_warning")
        summary    = r.get("summary", "")
        rank       = r.get("rank", i + 1)

        aligned_icon = "✅" if aligned else "⚠️"
        squeeze_icon = " 🔴" if squeeze else ""

        rows_html += (
            f'<tr style="background:{bg};">'
            f'<td style="{td}color:#94a3b8;">{rank}</td>'
            f'<td style="{td}font-weight:bold;">{pair}{squeeze_icon}</td>'
            f'<td style="{td}font-weight:bold;color:{_direction_color(direction)};">{direction}</td>'
            f'<td style="{td}color:{_confidence_color(confidence)};">{confidence}</td>'
            f'<td style="{td}">{aligned_icon}</td>'
            f'<td style="{td}color:#64748b;">{summary}</td>'
            f'</tr>'
        )

    detail_html = ""
    for r in ranked:
        pair = r.get("pair", "")
        ev   = eval_map.get(pair, {})
        if not ev:
            continue
        reasoning = ev.get("reasoning", "")
        if not reasoning:
            continue
        detail_html += (
            f'<h3 style="margin:24px 0 4px;color:#0f172a;font-size:14px;">{pair}</h3>'
            f'<p style="margin:0 0 8px;color:#334155;font-size:12px;line-height:1.5;">{reasoning}</p>'
        )

    return f"""
    <div style="font-family:sans-serif;max-width:860px;margin:auto;padding:24px;background:#ffffff;">
      <h2 style="margin:0 0 4px;color:#0f172a;">Swing Trade Evaluator — {len(ranked)} pares</h2>
      <p style="margin:0 0 6px;color:#64748b;font-size:13px;">{timestamp} · {mode.upper()}</p>
      <p style="margin:0 0 24px;font-size:13px;color:#334155;">{summary_line}</p>

      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#1e293b;color:#f1f5f9;">
            <th style="{th}">#</th>
            <th style="{th}">Par</th>
            <th style="{th}">Dir.</th>
            <th style="{th}">Confianza</th>
            <th style="{th}">Alin.</th>
            <th style="{th}">Síntesis</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>

      <hr style="margin:28px 0;border:none;border-top:1px solid #e2e8f0;">
      <h3 style="margin:0 0 12px;color:#0f172a;font-size:14px;">Razonamientos</h3>
      {detail_html}

      <p style="margin-top:24px;color:#94a3b8;font-size:11px;text-align:center;">
        swing-trade-evaluator · Railway cron
      </p>
    </div>"""


# ── Email senders ─────────────────────────────────────────────────────────────

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


def _send(subject: str, html: str) -> None:
    if os.environ.get("RESEND_API_KEY"):
        send_email_resend(subject, html)
    else:
        send_email_smtp(subject, html)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate crypto trade direction")
    parser.add_argument(
        "pair", nargs="?", default=None,
        help="Single pair (e.g. BTC). Omit to evaluate the default 10-pair list.",
    )
    parser.add_argument(
        "--pairs",
        help="Comma-separated list of pairs (e.g. BTC,ETH,SOL). Overrides default list.",
    )
    parser.add_argument(
        "--mode",
        choices=["swing", "intraday"],
        default="swing",
        help="Evaluation mode: swing (default) or intraday",
    )
    args = parser.parse_args()

    email_enabled = bool(os.environ.get("RESEND_API_KEY") or os.environ.get("SMTP_USER"))
    if not email_enabled:
        print("Ni RESEND_API_KEY ni SMTP_USER configurados — email omitido.")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = "INTRADAY TRADE EVALUATION" if args.mode == "intraday" else "SWING TRADE EVALUATION"

    # ── Single pair ────────────────────────────────────────────────────────────
    if args.pair and not args.pairs:
        pair   = args.pair.upper()
        result = run_agent(pair, mode=args.mode)

        print("\n" + "=" * 70)
        print(header)
        print("=" * 70)
        print(json.dumps(result, indent=2))

        if email_enabled:
            direction  = result.get("direction", "N/A")
            confidence = result.get("confidence", "")
            squeeze    = result.get("squeeze_warning")

            if squeeze:
                subject = f"⚠️ {pair} {direction} — Squeeze Warning · {timestamp}"
            elif confidence == "high":
                subject = f"📊 {pair} {direction} high confidence · {timestamp}"
            else:
                subject = f"📊 {pair} {direction} {confidence} · {timestamp}"

            html = build_html_single(pair, args.mode, result, timestamp)
            try:
                _send(subject, html)
            except Exception as exc:
                print(f"Email no enviado: {exc}")

    # ── Multi-pair batch ───────────────────────────────────────────────────────
    else:
        if args.pairs:
            pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
        else:
            pairs = DEFAULT_PAIRS

        print(f"\n{header} — {len(pairs)} pares: {', '.join(pairs)}")
        print("=" * 70)

        evals  = run_agent_batch(pairs, mode=args.mode)
        ranked = run_synthesis(evals, mode=args.mode)
        ranked_list = ranked if isinstance(ranked, list) else []

        print(json.dumps({"ranked": ranked_list, "evaluations": evals}, indent=2))

        if email_enabled:
            squeeze_pairs = [r["pair"] for r in ranked_list if r.get("squeeze_warning")]
            high_conf     = [r for r in ranked_list if r.get("confidence") == "high" and r.get("aligned")]

            if squeeze_pairs:
                subject = f"⚠️ Swing Trade — Squeeze: {', '.join(squeeze_pairs)} · {timestamp}"
            elif high_conf:
                top = high_conf[0]
                subject = f"📊 Swing Trade — #{1} {top['pair']} {top['direction']} high · {timestamp}"
            else:
                subject = f"📊 Swing Trade Report ({len(pairs)} pares) · {timestamp}"

            html = build_html_batch(ranked_list, evals, args.mode, timestamp)
            try:
                _send(subject, html)
            except Exception as exc:
                print(f"Email no enviado: {exc}")
