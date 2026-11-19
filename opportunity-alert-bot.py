#!/usr/bin/env python3
"""
opportunity-alert-bot.py
=======================
Scans data/opportunities.jsonl for new earning opportunities across marketplaces
(0xWork, Nookplot bounties, MoltLaunch, etc.) and sends real-time alerts
when high-value or capability-matching tasks appear.

Alerts via (in priority order):
  1. Telegram bot API  (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)
  2. Discord webhook     (DISCORD_WEBHOOK_URL)
  3. File fallback       -> data/opportunity-alerts.log

Deduplication: tracks already-sent opportunity IDs in logs/alert-state.json
Rate-limiting: max 1 alert per 5 minutes globally to avoid spam.

Usage:
    python3 opportunity-alert-bot.py              # normal poll
    python3 opportunity-alert-bot.py --test       # send test alert
    python3 opportunity-alert-bot.py --dry-run    # show what would alert

Env vars:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_URL

Auto-pollers should append ND-JSON lines to data/opportunities.jsonl:
    {"id":"0xwork-42","source":"0xWork","title":"Base L2 audit","bounty":5.0,"currency":"USDC","url":"...","description":"...","tags":["audit"],"timestamp":"2026-05-07T07:42:00Z"}
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Data directory: override with AGENT_ALERTS_DATA_DIR, otherwise ./data
# (feed, dedupe state and fallback log all live here).
DATA_DIR = Path(os.environ.get("AGENT_ALERTS_DATA_DIR", ""))
if not DATA_DIR:
    DATA_DIR = Path(__file__).resolve().parent / "data"
OPPORTUNITIES_LOG = DATA_DIR / "opportunities.jsonl"
ALERT_STATE = DATA_DIR / "alert-state.json"
FILE_FALLBACK = DATA_DIR / "opportunity-alerts.log"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

RATE_LIMIT_SECONDS = 300  # 5 minutes

KEYWORDS_LOW = ["smart contract", "audit", "security", "base l2", "arbitrage", "defi"]
KEYWORDS_HIGH = ["research", "writing", "code", "data"]
ALL_KEYWORDS = KEYWORDS_LOW + KEYWORDS_HIGH

BOUNTY_THRESHOLD_LOW = 1.0   # USD-ish
BOUNTY_THRESHOLD_HIGH = 5.0  # definitely alert

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if ALERT_STATE.exists():
        try:
            with open(ALERT_STATE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"sent_ids": [], "last_alert_ts": None}

def save_state(state: dict):
    ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_STATE, "w") as f:
        json.dump(state, f, indent=2)

# ---------------------------------------------------------------------------
# Opportunity parsing
# ---------------------------------------------------------------------------
def parse_opportunities() -> list[dict]:
    """Read logs/opportunities.jsonl and return list of opportunity dicts."""
    if not OPPORTUNITIES_LOG.exists():
        return []
    ops = []
    with open(OPPORTUNITIES_LOG, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ops.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return ops

def opportunity_matches(op: dict) -> tuple[bool, str]:
    """
    Returns (matches, reason) if the opportunity should trigger an alert.
    """
    bounty = 0.0
    try:
        bounty = float(op.get("bounty", 0) or 0)
    except (TypeError, ValueError):
        pass

    text = " ".join([
        str(op.get("title", "")),
        str(op.get("description", "")),
        " ".join(str(t) for t in op.get("tags", []))
    ]).lower()

    if bounty >= BOUNTY_THRESHOLD_HIGH:
        return True, f"high bounty (${bounty:.2f})"

    for kw in ALL_KEYWORDS:
        if kw in text:
            if bounty >= BOUNTY_THRESHOLD_LOW:
                return True, f"keyword '{kw}' + bounty ${bounty:.2f}"
            if kw in KEYWORDS_LOW:
                return True, f"high-value keyword '{kw}'"

    if bounty >= BOUNTY_THRESHOLD_LOW:
        return True, f"bounty ${bounty:.2f}"

    return False, ""

# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------
def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[TELEGRAM ERR] {e}")
        return False

def send_discord(message: str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        payload = {"content": message}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "Manteclaw-AlertBot/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        print(f"[DISCORD ERR] {e}")
        return False

def file_fallback(message: str):
    """Append alert to local log when no chat channels are configured."""
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(FILE_FALLBACK, "a") as f:
        f.write(f"[{timestamp}] {message}\n{'='*40}\n")

# ---------------------------------------------------------------------------
# Alert orchestration
# ---------------------------------------------------------------------------
def build_alert_text(op: dict, reason: str) -> str:
    title = op.get("title", "Untitled Opportunity")
    source = op.get("source", "Unknown")
    bounty = op.get("bounty")
    currency = op.get("currency", "USD")
    url = op.get("url", "")
    desc = op.get("description", "")

    lines = [
        f"💰 *New Earning Opportunity*",
        f"",
        f"*{title}*",
        f"Source: `{source}`",
    ]
    if bounty is not None:
        lines.append(f"Bounty: `{bounty} {currency}`")
    lines.append(f"Match: `{reason}`")
    if desc:
        snippet = desc[:200] + "..." if len(desc) > 200 else desc
        lines.append(f"_{snippet}_")
    if url:
        lines.append(f"[Link]({url})")
    lines.append(f"\n⏱ {datetime.now(timezone.utc).isoformat()}")
    return "\n".join(lines)

def send_alert(text: str) -> bool:
    """Try Telegram → Discord → file fallback. Returns True if any channel succeeded."""
    ok = False
    if send_telegram(text):
        print("[ALERT] Telegram sent")
        ok = True
    if send_discord(text):
        print("[ALERT] Discord sent")
        ok = True
    if not ok:
        file_fallback(text)
        print(f"[ALERT] Logged to {FILE_FALLBACK}")
    return ok

def check_rate_limit(state: dict) -> bool:
    last = state.get("last_alert_ts")
    if last is None:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return True
    return datetime.now(timezone.utc) - last_dt >= timedelta(seconds=RATE_LIMIT_SECONDS)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Opportunity alert bot")
    parser.add_argument("--test", action="store_true", help="Send a test alert and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be alerted without sending")
    args = parser.parse_args()

    state = load_state()

    if args.test:
        test_op = {
            "id": "test-op-001",
            "source": "TestMarket",
            "title": "Smart contract audit on Base L2",
            "bounty": 12.5,
            "currency": "USDC",
            "url": "https://example.com/task/1",
            "description": "Audit a new vault contract for re-entrancy bugs.",
            "tags": ["audit", "security", "Base L2"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        text = build_alert_text(test_op, "test mode")
        print("[TEST] Alert text preview:\n" + text)
        if not args.dry_run:
            send_alert(text)
            state["sent_ids"].append(test_op["id"])
            state["last_alert_ts"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
        return

    # Normal poll mode
    opportunities = parse_opportunities()
    if not opportunities:
        print(f"[INFO] No opportunities found in {OPPORTUNITIES_LOG}")
        print("[INFO] Auto-pollers should append ND-JSON lines to this file.")
        return

    sent_ids = set(state.get("sent_ids", []))
    new_matches = []

    for op in opportunities:
        op_id = op.get("id")
        if not op_id:
            continue
        if op_id in sent_ids:
            continue
        matches, reason = opportunity_matches(op)
        if matches:
            new_matches.append((op, reason))

    if not new_matches:
        print("[INFO] No new matching opportunities.")
        return

    # Rate limit: only alert the highest-bounty match if under rate limit
    if not check_rate_limit(state):
        print("[RATE LIMIT] Under cooldown. Skipping alert.")
        # Still mark them as seen so we don't spam them later
        for op, _ in new_matches:
            sent_ids.add(op.get("id"))
        state["sent_ids"] = list(sent_ids)
        save_state(state)
        return

    # Pick the best one (highest bounty, or first if no bounty)
    def sort_key(item):
        b = item[0].get("bounty", 0) or 0
        try:
            return float(b)
        except (TypeError, ValueError):
            return 0.0

    new_matches.sort(key=sort_key, reverse=True)
    best_op, best_reason = new_matches[0]

    text = build_alert_text(best_op, best_reason)
    print(f"[ALERT] {best_op.get('id')} -> {best_reason}")
    if args.dry_run:
        print("[DRY-RUN] Would send:\n" + text)
        return
    send_alert(text)

    # Mark all new matches as sent so we don't re-process
    for op, _ in new_matches:
        sent_ids.add(op.get("id"))
    state["sent_ids"] = list(sent_ids)
    state["last_alert_ts"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    remaining = len(new_matches) - 1
    if remaining > 0:
        print(f"[INFO] {remaining} other matching opportunity(s) queued but suppressed by rate limit.")

if __name__ == "__main__":
    main()
