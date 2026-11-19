#!/usr/bin/env python3
"""
Manteclaw Alert Bot
===================
Sends alerts to Telegram, Discord, and Email when:
- Arbitrage opportunity > threshold found
- Wallet gas below threshold
- Miner goes offline
- Any healthcheck fails
- 0xWork non-social task appears
- Litcoiin anti-farming flag cleared

Usage:
    TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=xxx MAILERSEND_API_KEY=xxx python3 alert_bot.py
    python3 alert_bot.py --test  # Send test alerts to all channels

Requires:
    pip install python-telegram-bot aiohttp
"""

import os
import sys
import json
import argparse
import asyncio
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

# Try telegram bot, fallback to simple HTTP if not installed
try:
    from telegram import Bot
    USE_SDK = True
except ImportError:
    USE_SDK = False

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
MAILERSEND_API_KEY = os.getenv("MAILERSEND_API_KEY", "")
MAILERSEND_FROM_EMAIL = os.getenv("MAILERSEND_FROM_EMAIL", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")

class AlertBot:
    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.discord_webhook = DISCORD_WEBHOOK
        self.mailersend_key = MAILERSEND_API_KEY
        self.mailersend_from = MAILERSEND_FROM_EMAIL
        self.alert_email_to = ALERT_EMAIL_TO
        self._bot: Optional[Bot] = None

    async def _send_telegram(self, message: str) -> bool:
        if not self.token or not self.chat_id:
            print(f"[TELEGRAM SKIP] No token/chat_id. Msg: {message[:60]}...")
            return False
        try:
            if USE_SDK:
                if not self._bot:
                    self._bot = Bot(self.token)
                await self._bot.send_message(chat_id=self.chat_id, text=message, parse_mode="Markdown")
            else:
                import urllib.request
                url = f"https://api.telegram.org/bot{self.token}/sendMessage"
                data = json.dumps({"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}).encode()
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            print(f"[TELEGRAM ERR] {e}")
            return False

    async def _send_discord(self, message: str) -> bool:
        if not self.discord_webhook:
            return False
        try:
            import urllib.request
            data = json.dumps({"content": message}).encode()
            req = urllib.request.Request(self.discord_webhook, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            print(f"[DISCORD ERR] {e}")
            return False

    async def _send_email(self, title: str, message: str, priority: str) -> bool:
        """Send alert via MailerSend API."""
        if not self.mailersend_key or not self.mailersend_from or not self.alert_email_to:
            return False
        try:
            import urllib.request
            url = "https://api.mailersend.com/v1/email"
            payload = json.dumps({
                "from": {"email": self.mailersend_from, "name": "Manteclaw Alerts"},
                "to": [{"email": self.alert_email_to, "name": "Manteclaw Admin"}],
                "subject": f"[{priority.upper()}] {title}",
                "text": f"{title}\n\n{message}\n\nTime: {datetime.now(timezone.utc).isoformat()}"
            }).encode()
            req = urllib.request.Request(url, data=payload, headers={
                "Authorization": f"Bearer {self.mailersend_key}",
                "Content-Type": "application/json",
                "User-Agent": "ManteclawAlertBot/1.0"
            })
            resp = urllib.request.urlopen(req, timeout=15)
            return resp.status == 202
        except Exception as e:
            print(f"[EMAIL ERR] {e}")
            return False

    async def alert(self, title: str, message: str, priority: str = "normal"):
        """Send alert to all configured channels."""
        emoji = {"low": "ℹ️", "normal": "⚡", "high": "🔴", "critical": "🚨"}.get(priority, "⚡")
        full = f"{emoji} *{title}*\n{message}\n\n_{datetime.now(timezone.utc).isoformat()}_"
        ok_tg = await self._send_telegram(full)
        ok_dc = await self._send_discord(f"{emoji} **{title}**\n{message}")
        ok_email = await self._send_email(title, message, priority)
        if not ok_tg and not ok_dc and not ok_email:
            print(f"[ALERT] {title}: {message}")

    async def alert_arb(self, spread_pct: float, pair: str, dex1: str, dex2: str, profit_eth: float):
        await self.alert(
            f"Arb Opportunity: {pair}",
            f"Spread: `{spread_pct:.2f}%`\n"
            f"DEXs: `{dex1}` → `{dex2}`\n"
            f"Est. profit: `{profit_eth:.6f} ETH`",
            "high" if spread_pct > 1.0 else "normal"
        )

    async def alert_low_gas(self, wallet: str, balance_eth: float, threshold: float):
        await self.alert(
            f"Low Gas: {wallet[:10]}...",
            f"Balance: `{balance_eth:.6f} ETH`\n"
            f"Threshold: `{threshold:.4f} ETH`\n"
            f"Refuel needed!",
            "critical" if balance_eth < threshold / 2 else "high"
        )

    async def alert_miner_down(self, miner_name: str, last_seen_minutes: int):
        await self.alert(
            f"Miner Offline: {miner_name}",
            f"Last seen: `{last_seen_minutes}m` ago\n"
            f"Check: `systemctl status manteclaw-{miner_name}`",
            "high"
        )

    async def alert_health_fail(self, check_name: str, detail: str):
        await self.alert(
            f"Health Fail: {check_name}",
            f"Detail: `{detail}`\n"
            f"Run: `python3 scripts/healthcheck.py`",
            "high"
        )

    async def alert_0xwork_task(self, task_id: str, bounty: float, category: str, description: str):
        """Alert when a non-social task appears on 0xWork."""
        await self.alert(
            f"0xWork Task Available: #{task_id}",
            f"Category: `{category}`\n"
            f"Bounty: `${bounty}`\n"
            f"Description: {description[:200]}...",
            "high"
        )

    async def alert_agenthansa_quests(self, new_count: int, total_available: int, top_quests: list):
        """Alert when new Alliance War quests are available."""
        lines = [f"• `{q['title'][:40]}` — ${q['reward']}" for q in top_quests[:5]]
        await self.alert(
            f"AgentHansa: {new_count} New Quests",
            f"Total available: `{total_available}`\n" + "\n".join(lines) + "\n\nSubmit via `POST /api/alliance-war/quests/{id}/submit`",
            "normal"
        )

    async def alert_agenthansa_daily(self, incomplete: int, bonus_points: int):
        """Alert when daily quests are incomplete."""
        if incomplete == 0:
            return
        await self.alert(
            f"AgentHansa: {incomplete} Daily Quests Pending",
            f"Quests: checkin · create · curate · distribute · digest\n"
            f"Complete all 5 for `+{bonus_points} bonus points`",
            "normal" if incomplete > 2 else "high"
        )

    async def alert_agenthansa_onboarding(self, steps_remaining: int, bonus: float):
        """Alert when onboarding is incomplete."""
        if steps_remaining == 0:
            return
        await self.alert(
            f"AgentHansa: {steps_remaining} Onboarding Steps Left",
            f"Complete to earn `${bonus:.2f}` bonus:\n"
            f"1. Set FluxA wallet\n"
            f"2. Generate referral link\n"
            f"3. Post in forum\n"
            f"4. Choose alliance (red/blue/green)",
            "normal"
        )

    async def alert_agenthansa_earnings_milestone(self, total_earned: float, rank: int, total_agents: int):
        """Alert on earnings milestone or rank improvement."""
        pct = (total_agents - rank) / total_agents * 100 if total_agents else 0
        await self.alert(
            f"AgentHansa: Earnings Update",
            f"Total earned: `${total_earned:.2f}`\n"
            f"Rank: `{rank:,} / {total_agents:,}` ({pct:.1f}th percentile)",
            "normal"
        )

    async def alert_agenthansa_arena(self, quest_count: int, top_quests: list):
        """Alert when Alliance War quests have open slots."""
        if quest_count == 0:
            return
        lines = [f"• `{q['title'][:40]}` — ${q.get('reward_amount', 0)} | {q.get('slots_remaining', 0)} slots" for q in top_quests[:5]]
        await self.alert(
            f"🎮 AgentHansa ARENA: {quest_count} quests OPEN",
            f"Alliance War quests with available slots:\n" + "\n".join(lines) +
            f"\n\nSubmit: `python3 projects/agenthansa/arena_hunter.py --submit <quest_id> --content '...' --proof '...'`",
            "high"
        )
    async def alert_agenthansa_help_tasks(self, count: int):
        """Alert when help-board tasks are available."""
        await self.alert(
            f"AgentHansa: {count} Help Tasks Open",
            f"Pro-bono tasks on the help board. Earn reputation.\n"
            f"GET `/api/help/agent-feed` → POST `/api/help/requests/{{id}}/respond`",
            "low"
        )

    async def alert_antifarming_cleared(self, wallet: str):
        await self.alert(
            f"✅ Litcoiin Anti-Farming CLEARED",
            f"Wallet `{wallet}` has been unflagged!\n"
            f"Miner can earn again. Resume operations.",
            "critical"
        )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Send test alerts")
    parser.add_argument("--arb", type=float, help="Simulate arb alert with spread %")
    parser.add_argument("--gas", type=float, help="Simulate low gas alert")
    args = parser.parse_args()

    bot = AlertBot()

    if args.test:
        await bot.alert("Test Alert", "Manteclaw alert bot is online and configured correctly.", "normal")
        return

    if args.arb:
        await bot.alert_arb(args.arb, "WETH/USDC", "Uniswap V3", "Aerodrome", 0.002)
        return

    if args.gas is not None:
        await bot.alert_low_gas("example-wallet", args.gas, 0.005)
        return

    print("AlertBot ready. Use --test, --arb, or --gas to send alerts.")
    print(f"Telegram: {'✅' if TELEGRAM_BOT_TOKEN else '❌ not configured'}")
    print(f"Discord:  {'✅' if DISCORD_WEBHOOK else '❌ not configured'}")
    print(f"Email:    {'✅' if MAILERSEND_API_KEY else '❌ not configured'}")

if __name__ == "__main__":
    asyncio.run(main())
