#!/usr/bin/env python3
"""Hermes Trading Report - live Freqtrade execution report.

Hermes decisions and Freqtrade execution are intentionally reported separately.
The Freqtrade API is the source of truth for open trades, balances and PnL.
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

import requests

BRAIN_STATE = "/root/.hermes/profiles/trader/freqtrade/hermes_state.json"
PREV_STATE = "/root/.hermes/profiles/trader/freqtrade/prev_report.json"
CRON_LOG = "/root/.hermes/profiles/trader/cron_output.log"
FREQTRADE_URL = os.environ.get("FREQTRADE_URL", "http://127.0.0.1:8080")
FREQTRADE_USER = os.environ.get("FREQTRADE_USER", "hermes")
FREQTRADE_PASSWORD = os.environ.get("FREQTRADE_PASSWORD", "hermes123")


def load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def api_get(path: str) -> Tuple[Any, str]:
    try:
        response = requests.get(
            f"{FREQTRADE_URL}{path}",
            auth=(FREQTRADE_USER, FREQTRADE_PASSWORD),
            timeout=10,
        )
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}: {response.text[:160]}"
        return response.json(), ""
    except Exception as exc:
        return None, str(exc)


def latest_cycle_log() -> str:
    try:
        text = open(CRON_LOG, encoding="utf-8", errors="replace").read()
        chunks = re.split(r"(?=^===== )", text, flags=re.MULTILINE)
        # A report can run while the next five-minute cycle is already in
        # progress. Use the newest completed cycle, not the newest timestamped
        # chunk, otherwise a partial cycle hides the previous execution.
        completed = [chunk for chunk in chunks if "Cycle completed" in chunk]
        return completed[-1] if completed else ""
    except Exception:
        return ""


def execution_counts(log: str) -> Dict[str, int]:
    """Count actual controller attempts/results from the latest cycle only."""
    # A skipped order was never sent to Freqtrade.  Only successful or failed
    # BUY requests count as orders sent; local duplicate/max-open skips are
    # reported separately.
    decisions_sent = len(re.findall(r"🟢 BUY [^\n]+|BUY failed [^\n]+", log))
    accepted = len(re.findall(r"🟢 BUY [^\n]+", log))
    rejected = len(re.findall(r"BUY failed [^\n]+", log))
    skipped = len(re.findall(r"BUY skipped[^\n]+", log))
    sells_accepted = len(re.findall(r"🔴 SELL Trade #", log))
    return {
        "buy_sent": decisions_sent,
        "buy_accepted": accepted,
        "buy_rejected": rejected,
        "buy_skipped": skipped,
        "sell_accepted": sells_accepted,
    }


def rejection_reasons(log: str) -> List[str]:
    reasons: List[str] = []
    for line in log.splitlines():
        if "BUY failed" in line or "BUY skipped" in line:
            clean = re.sub(r"^\s*", "", line)
            reasons.append(clean[:220])
    return reasons[-8:]


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def trade_line(trade: Dict[str, Any]) -> str:
    trade_id = trade.get("trade_id", "?")
    pair = trade.get("pair", "?")
    stake = number(trade.get("stake_amount"))
    entry = number(trade.get("open_rate", trade.get("open_rate_requested")))
    current = number(trade.get("current_rate"))
    pnl_pct = number(trade.get("profit_pct"))
    pnl_abs = number(trade.get("profit_abs"))
    return (
        f"#{trade_id} {pair} | stake ${stake:,.2f} | entry ${entry:,.8g} "
        f"| now ${current:,.8g} | PnL {pnl_pct:+.2f}% (${pnl_abs:+,.2f})"
    )


def generate_report() -> str:
    now = datetime.now()
    brain = load_json(BRAIN_STATE)
    prev = load_json(PREV_STATE)
    log = latest_cycle_log()

    ping, ping_error = api_get("/api/v1/ping")
    status, status_error = api_get("/api/v1/status")
    balance, balance_error = api_get("/api/v1/balance")
    profit, profit_error = api_get("/api/v1/profit")

    decisions = brain.get("last_decisions") or {}
    decision_values = [d for d in decisions.values() if isinstance(d, dict)]
    buy_decisions = sum(d.get("action") == "buy" for d in decision_values)
    sell_decisions = sum(d.get("action") == "sell" for d in decision_values)
    neutral_decisions = sum(d.get("action") in {"neutral", None} for d in decision_values)

    live_trades = status if isinstance(status, list) else []
    counts = execution_counts(log)
    rejects = rejection_reasons(log)

    provider = "nararouter"
    analysis_model = "grok-4.5"
    decision_model = "agnes-2.5-flash"
    source_values = [d.get("decision_source") for d in decision_values]
    if any(s == f"{decision_model}@{provider}" for s in source_values):
        provider_model = f"{analysis_model} analysis -> {decision_model} decisions @{provider}"
    else:
        provider_model = f"{analysis_model} analysis -> {decision_model} decisions @{provider}"

    realized = number((profit or {}).get("profit_closed_coin"))
    unrealized = number((profit or {}).get("profit_open_coin"))
    total_profit = number((profit or {}).get("profit_all_coin"), realized + unrealized)
    available = number((balance or {}).get("available_capital"))
    # Freqtrade's /balance schema exposes simulated currencies and aggregate
    # totals, not available_capital/used_capital fields.  Derive these from
    # the API response and label them precisely below.
    currencies = (balance or {}).get("currencies") if isinstance(balance, dict) else []
    usdt = next((c for c in currencies if c.get("currency") == "USDT"), {})
    available = number(usdt.get("free"), number((balance or {}).get("value_bot")))
    used = sum(number(c.get("est_stake_bot")) for c in currencies if c.get("currency") != "USDT")
    total_capital = number((balance or {}).get("value_bot"), available + used)

    previous = prev if isinstance(prev, dict) else {}
    def comparison(label: str, current: float, key: str) -> str:
        old = previous.get(key)
        old_text = "غير متاح" if old is None else f"${float(old):,.2f}"
        delta_text = "غير متاح" if old is None else f"${current - float(old):+,.2f}"
        return (
            f"{label}:\n"
            f"  الحالي: ${current:,.2f}\n"
            f"  السابق: {old_text}\n"
            f"  التغير: {delta_text}"
        )

    # Preserve the user's comparative report state, but never use it as execution truth.
    save_json(PREV_STATE, {
        "time": now.strftime("%H:%M"),
        "available": available,
        "used": used,
        "total_capital": total_capital,
        "realized": realized,
        "unrealized": unrealized,
        "total_profit": total_profit,
        "open_count": len(live_trades),
    })

    lines = [f"🤖 Hermes Trading Report | {now.strftime('%Y-%m-%d %H:%M UTC')}"]
    if ping_error or status_error:
        lines.append(f"⚠️ Freqtrade API: {'ping ' + ping_error if ping_error else status_error}")
    else:
        lines.append("✅ Freqtrade API: connected")
    lines.append(f"🧠 Provider/model: {provider_model}")
    lines.append("")

    lines.append("🧠 قرارات Hermes — ليست صفقات منفذة")
    lines.append(f"BUY: {buy_decisions} | SELL: {sell_decisions} | NEUTRAL: {neutral_decisions}")
    lines.append("")

    lines.append("⚙️ تنفيذ Freqtrade — مصدر الحقيقة")
    lines.append(f"أوامر BUY أرسلها Hermes: {counts['buy_sent']}")
    lines.append(f"أوامر BUY قبلها Freqtrade: {counts['buy_accepted']}")
    lines.append(f"أوامر BUY مرفوضة: {counts['buy_rejected']} | متجاوزة محلياً: {counts['buy_skipped']}")
    lines.append(f"صفقات مفتوحة فعلياً /status: {len(live_trades)}")

    if rejects:
        lines.append("أسباب الرفض/التجاوز:")
        lines.extend(f"• {reason}" for reason in rejects[-4:])
    elif counts["buy_accepted"] or counts["sell_accepted"]:
        actions = []
        if counts["buy_accepted"]:
            actions.append(f"تم قبول BUY عدد {counts['buy_accepted']}")
        if counts["sell_accepted"]:
            actions.append(f"تم قبول SELL عدد {counts['sell_accepted']}")
        lines.append("نتيجة التنفيذ: " + "، ".join(actions) + " من Freqtrade")
    elif counts["buy_sent"] or counts["buy_rejected"] or counts["buy_skipped"]:
        lines.append("نتيجة التنفيذ: تمت محاولة تنفيذ ولم تُقبل أوامر جديدة")
    else:
        lines.append("نتيجة التنفيذ: لا توجد محاولة BUY/SELL في آخر دورة")

    lines.append("")
    lines.append("💼 Freqtrade Balance/PnL")
    lines.append(comparison("متاح USDT", available, "available"))
    lines.append(comparison("مستخدم في الصفقات", used, "used"))
    lines.append(comparison("قيمة الحساب", total_capital, "total_capital"))
    lines.append(comparison("الكلي", total_profit, "total_profit"))

    lines.append("")
    lines.append(f'📦 عدد الصفقات المفتوحة: {len(live_trades)}')

    regime = (decisions.get("BTC/USDT") or {}).get("regime")
    blocked = []
    if regime == "llm_unavailable" or any(d.get("decision_source") == "none" for d in decision_values):
        blocked.append("LLM unavailable")
    if any("balance" in r.lower() or "stake" in r.lower() for r in rejects):
        blocked.append("balance insufficient")
    # Do not infer a current-cycle block merely from the number of open trades;
    # the latest cycle may have produced only neutral decisions or been blocked
    # by the LLM. Report max_open_trades only when the cycle logged that reason.
    if any("maximum open trades" in r.lower() for r in rejects):
        blocked.append("max_open_trades")
    lines.append("")
    lines.append(f"⛔ سبب منع التداول: {', '.join(dict.fromkeys(blocked)) if blocked else 'لا يوجد منع مثبت في آخر دورة'}")
    lines.append("━" * 34)
    return "\n".join(lines)


if __name__ == "__main__":
    print(generate_report())
