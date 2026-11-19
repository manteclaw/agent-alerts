# agent-alerts

Async alert dispatcher, earning-opportunity scanner, and API-key health
tracker for anyone running agents or services that need to shout when
something breaks.

Three small scripts, one job each:

| Script | What it does |
| --- | --- |
| `alert_bot.py` | Generic alert dispatcher — Telegram, Discord, and email (MailerSend). Call it from your own health checks, miners, or bots. |
| `opportunity-alert-bot.py` | Watches a JSONL feed of earning opportunities (bounties/tasks) and alerts on the ones worth your time, with dedupe and rate-limiting. |
| `api-health.py` | Checks the API keys you actually use, reports which are OK / DEAD / MISSING, and alerts (via `alert_bot.py`) only when a key's state changes. |

## Install

```bash
git clone https://github.com/manteclaw/agent-alerts.git
cd agent-alerts
python3 -m pip install -r requirements.txt   # optional — see below
cp .env.example .env                          # then fill in your own values
```

Requires Python 3.10+. The scanner and the health tracker are
**stdlib-only**. The dispatcher works with stdlib only too (plain HTTPS
calls to the Telegram Bot API); installing `python-telegram-bot` just
switches it to the SDK path, and `python-dotenv` just auto-loads `.env`.
Neither is required.

## Usage

### `alert_bot.py` — dispatcher

```bash
python3 alert_bot.py                       # show which channels are configured
python3 alert_bot.py --test                # send a test alert to all channels
python3 alert_bot.py --arb 1.5             # simulate an arbitrage alert (1.5% spread)
python3 alert_bot.py --gas 0.002           # simulate a low-gas alert (0.002 ETH)
```

With no channels configured it prints the alert to stdout and exits
cleanly — nothing is sent anywhere. Import it from your own code:

```python
import asyncio
from alert_bot import AlertBot

asyncio.run(AlertBot().alert("Miner Offline", "litcoiin miner last seen 42m ago", "high"))
```

Alert helpers included: `alert_arb`, `alert_low_gas`, `alert_miner_down`,
`alert_health_fail`, `alert_0xwork_task`, `alert_antifarming_cleared`,
plus AgentHansa quest/onboarding/earnings/arena/help-task alerts.
Priority levels: `low`, `normal`, `high`, `critical`.

### `opportunity-alert-bot.py` — opportunity scanner

```bash
python3 opportunity-alert-bot.py            # normal poll (meant for cron, every 5 min)
python3 opportunity-alert-bot.py --test     # send a test alert
python3 opportunity-alert-bot.py --dry-run  # show what would alert, without sending
```

It reads the feed at `data/opportunities.jsonl` (see below), picks the
best new match, and sends **at most one alert per 5 minutes**. Run it
from cron:

```cron
*/5 * * * * cd /path/to/agent-alerts && python3 opportunity-alert-bot.py >> data/opportunity-alert-bot.log 2>&1
```

### `api-health.py` — API-key health tracker

```bash
python3 api-health.py
```

Prints a one-line summary (`OK=.. DEAD=.. MISSING=.. WARN=..`), writes a
full JSON report to `data/.state/api-health.json`, and alerts via
`alert_bot.py` only on state changes (key goes DEAD, disappears, or
recovers). Exit code is the count of DEAD + MISSING keys (0 = all
configured keys healthy). Providers checked: Groq, Cerebras, NVIDIA,
DeepSeek, Mistral, OpenRouter, Venice, Bankr, Nookplot, Alchemy, SambaNova,
Moonshot, GitHub, Dune. A key set to `__RUNTIME_ONLY__` is reported as
`RUNTIME_ONLY` and never probed over the network.

## Environment variables

| Variable | Used by | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | dispatcher, scanner | Telegram bot token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | dispatcher, scanner | Chat/channel ID to send to |
| `DISCORD_WEBHOOK_URL` | dispatcher, scanner | Discord webhook URL |
| `MAILERSEND_API_KEY` | dispatcher | MailerSend API key for email alerts |
| `MAILERSEND_FROM_EMAIL` | dispatcher | Sender address for email alerts |
| `ALERT_EMAIL_TO` | dispatcher | Recipient address for email alerts |
| `AGENT_ALERTS_DATA_DIR` | scanner, health tracker | Where the feed, state, and logs live (default: `./data`) |
| `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `NVIDIA_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`, `VENICE_INFERENCE_KEY`, `BANKR_API_KEY`, `NOOKPLOT_API_KEY`, `ALCHEMY_API_KEY`, `SAMBANOVA_API_KEY`, `MOONSHOT_API_KEY`, `GITHUB_TOKEN`, `DUNE_API_KEY` | health tracker | Keys to probe (unset = reported MISSING, never an error) |

All secrets come from the environment (or a local `.env` file, loaded if
`python-dotenv` is installed). Nothing is hardcoded — see `.env.example`.
Never commit `.env`.

## The JSONL feed contract

Auto-pollers append one JSON object per line to
`data/opportunities.jsonl` (override with `AGENT_ALERTS_DATA_DIR`):

```json
{"id": "example-42", "source": "ExampleMarket", "title": "Base L2 audit", "bounty": 5.0, "currency": "USDC", "url": "https://example.com/tasks/42", "description": "Audit a vault contract.", "tags": ["audit", "security"], "timestamp": "2026-05-07T07:42:00Z"}
```

- **Required fields:** `id`, `source`, `title`.
- **Recommended fields:** `bounty`, `currency`, `url`, `description`,
  `tags`, `timestamp`.
- Lines that are blank or not valid JSON are skipped silently.
- Opportunities without an `id` are ignored (they can't be deduped).

### Matching — what triggers an alert

- **Bounty ≥ $5** → always alert.
- **Keyword match + bounty ≥ $1** → alert. Keywords: `smart contract`,
  `audit`, `security`, `base l2`, `arbitrage`, `defi`, `research`,
  `writing`, `code`, `data` (matched case-insensitively against title +
  description + tags).
- **High-value keyword** (`smart contract`, `audit`, `security`,
  `base l2`, `arbitrage`, `defi`) → alert even with no bounty listed.
- **Any bounty ≥ $1** → alert.

### Dedupe and rate-limiting

- **Dedupe:** every alerted opportunity `id` is recorded in
  `data/alert-state.json` (`sent_ids`). An id is never alerted twice,
  even across restarts.
- **Rate limit:** at most **one alert per 5 minutes** globally
  (`RATE_LIMIT_SECONDS = 300`). When several new opportunities match,
  only the highest-bounty one is sent; the rest are marked as seen so
  they don't pile up. If a poll lands inside the cooldown window, new
  matches are marked as seen and skipped — no backlog, no spam.
- **Channels, in priority order:** Telegram → Discord → file fallback.
  If neither chat channel is configured (or both fail), the alert is
  appended to `data/opportunity-alerts.log` instead.

A synthetic feed to try it with lives in `examples/opportunities.jsonl`:

```bash
mkdir -p /tmp/alerts-demo
cp examples/opportunities.jsonl /tmp/alerts-demo/
AGENT_ALERTS_DATA_DIR=/tmp/alerts-demo python3 opportunity-alert-bot.py --dry-run
```

## State and log files

Everything the scripts write goes under `data/` (or
`$AGENT_ALERTS_DATA_DIR`):

```
data/
  opportunities.jsonl       # input feed (written by your pollers, not by these scripts)
  alert-state.json          # dedupe ids + last alert timestamp (scanner)
  opportunity-alerts.log    # file-fallback alerts (scanner)
  .state/
    api-health.json         # latest full health report
    api-key-health.json     # previous run's results (for state-change detection)
    dead-apis.jsonl         # append-only log of DEAD results
  logs/
    alerts.log              # state-change alerts sent by api-health
```

## Honest limits

- **Alerting depends on pollers writing the feed.** The scanner only
  reads `data/opportunities.jsonl` — it doesn't fetch marketplaces
  itself. No feed, no alerts.
- **Telegram/Discord need a token/webhook.** Without them, alerts fall
  back to a local log file. That's by design, not a bug.
- **The health tracker shells out to `curl`** for its probes and reports
  a provider as `WARN(<code>)` for any non-200/401/403 HTTP code,
  including network failures (empty code). It checks key validity, not
  quota or remaining balance.
- **One alert per 5 minutes is a hard cap** in the scanner — during a
  burst of good opportunities you'll hear about the best one only.
- The dispatcher's AgentHansa helpers and the 0xWork/Litcoiin alerts
  reflect the marketplaces the original author used; the generic
  `alert()` method is the part meant for reuse anywhere.

## License

MIT — see `LICENSE`.
