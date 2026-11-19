#!/usr/bin/env python3
"""
API key health tracker. Checks all known providers and reports dead/expiring keys.
Enhancement: webhook alerts on state change, tracked in .state/api-key-health.json
Usage: python3 scripts/api-health.py
"""
import json, os, re, subprocess, sys, time, asyncio
from datetime import datetime, timezone
from pathlib import Path

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# State/report paths: override with AGENT_ALERTS_DATA_DIR, otherwise ./data.
DATA_DIR = Path(os.environ.get("AGENT_ALERTS_DATA_DIR", ""))
if not DATA_DIR:
    DATA_DIR = Path(__file__).resolve().parent / "data"
REPORT_PATH = DATA_DIR / ".state" / "api-health.json"
HEALTH_TRACKER = DATA_DIR / ".state" / "api-key-health.json"
DEAD_LOG = DATA_DIR / ".state" / "dead-apis.jsonl"
ALERT_LOG = DATA_DIR / "logs" / "alerts.log"

PROVIDERS = {
    "groq": {"env": "GROQ_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://api.groq.com/openai/v1/models", "rotate_url": "https://console.groq.com/keys"},
    "cerebras": {"env": "CEREBRAS_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://api.cerebras.ai/v1/models", "rotate_url": "https://cloud.cerebras.ai/platform/keys"},
    "nvidia": {"env": "NVIDIA_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://integrate.api.nvidia.com/v1/models", "rotate_url": "https://build.nvidia.com/"},
    "deepseek": {"env": "DEEPSEEK_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://api.deepseek.com/models", "rotate_url": "https://platform.deepseek.com/"},
    "mistral": {"env": "MISTRAL_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://api.mistral.ai/v1/models", "rotate_url": "https://console.mistral.ai/"},
    "openrouter": {"env": "OPENROUTER_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://openrouter.ai/api/v1/models", "rotate_url": "https://openrouter.ai/keys"},
    "venice": {"env": "VENICE_INFERENCE_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://api.venice.ai/api/v1/models", "rotate_url": "https://venice.ai/settings/api"},
    "bankr": {"env": "BANKR_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://api.bankr.bot/v1/agents", "rotate_url": "https://bankr.bot/dashboard"},
    "nookplot": {"env": "NOOKPLOT_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://gateway.nookplot.com/v1/agents", "rotate_url": "https://nookplot.com/settings"},
    "alchemy": {"env": "ALCHEMY_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_blockNumber\"}' https://base-mainnet.g.alchemy.com/v2/{key}", "rotate_url": "https://dashboard.alchemy.com/"},
    "sambanova": {"env": "SAMBANOVA_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://api.sambanova.ai/v1/models", "rotate_url": "https://cloud.sambanova.ai/"},
    "moonshot": {"env": "MOONSHOT_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://api.moonshot.cn/v1/models", "rotate_url": "https://platform.moonshot.cn/"},
    "github": {"env": "GITHUB_TOKEN", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer {key}' https://api.github.com/user", "rotate_url": "https://github.com/settings/tokens"},
    "dune": {"env": "DUNE_API_KEY", "test": "curl -s -o /dev/null -w '%{http_code}' -H 'X-DUNE-API-KEY: {key}' https://api.dune.com/api/v1/query/execute", "rotate_url": "https://dune.com/settings/api"},
}

def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

def check_provider(name, cfg):
    key = os.environ.get(cfg["env"], "")
    if key == "__RUNTIME_ONLY__":
        return {"name": name, "status": "RUNTIME_ONLY", "code": None, "env": cfg["env"], "checked": datetime.now(timezone.utc).isoformat(), "rotate_url": cfg.get("rotate_url", "")}
    if not key:
        return {"name": name, "status": "MISSING", "code": None, "checked": datetime.now(timezone.utc).isoformat(), "rotate_url": cfg.get("rotate_url", "")}
    test_cmd = cfg["test"].replace("{key}", key)
    code = run(test_cmd).stdout.strip()
    status = "OK" if code == "200" else "DEAD" if code in ("401", "403") else f"WARN({code})"
    return {"name": name, "status": status, "code": code, "env": cfg["env"], "checked": datetime.now(timezone.utc).isoformat(), "rotate_url": cfg.get("rotate_url", "")}

def load_previous_state():
    try:
        with open(HEALTH_TRACKER) as f:
            data = json.load(f)
            return data.get("results", {})
    except Exception:
        return {}

def save_state(results):
    state = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": {r["name"]: r for r in results}
    }
    os.makedirs(os.path.dirname(HEALTH_TRACKER), exist_ok=True)
    with open(HEALTH_TRACKER, "w") as f:
        json.dump(state, f, indent=2)

async def send_webhook_alert(alerts):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from alert_bot import AlertBot
        bot = AlertBot()
        for alert in alerts:
            await bot.alert(alert["title"], alert["message"], alert["priority"])
    except Exception as e:
        print(f"[api-health] Webhook alert failed: {e}")

def log_alerts(alerts):
    os.makedirs(os.path.dirname(ALERT_LOG), exist_ok=True)
    with open(ALERT_LOG, "a") as fh:
        for alert in alerts:
            fh.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": alert["priority"].upper(),
                "source": "api-health",
                **alert
            }) + "\n")

def main():
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    
    prev_state = load_previous_state()
    results = []
    alerts = []
    
    for name, cfg in PROVIDERS.items():
        r = check_provider(name, cfg)
        results.append(r)
        
        # Check for state change: was OK/RUNTIME_ONLY, now DEAD
        prev = prev_state.get(name, {})
        prev_status = prev.get("status", "UNKNOWN")
        
        if r["status"] == "DEAD" and prev_status in ("OK", "RUNTIME_ONLY", "UNKNOWN", "MISSING"):
            # State change to DEAD
            alerts.append({
                "title": f"API Key Dead: {name}",
                "message": (
                    f"Provider: `{name}`\n"
                    f"Env var: `{cfg['env']}`\n"
                    f"HTTP code: `{r['code']}`\n"
                    f"Action: Rotate key at {cfg.get('rotate_url', 'provider dashboard')}"
                ),
                "priority": "high"
            })
        elif r["status"] == "MISSING" and prev_status in ("OK", "RUNTIME_ONLY", "UNKNOWN"):
            # Key disappeared
            alerts.append({
                "title": f"API Key Missing: {name}",
                "message": (
                    f"Provider: `{name}`\n"
                    f"Env var: `{cfg['env']}` no longer set\n"
                    f"Action: Re-export key or check .env file"
                ),
                "priority": "high"
            })
        elif r["status"] == "OK" and prev_status in ("DEAD", "MISSING"):
            # Key recovered
            alerts.append({
                "title": f"API Key Recovered: {name}",
                "message": f"Provider `{name}` is back online.",
                "priority": "normal"
            })
        
        if r["status"] == "DEAD":
            with open(DEAD_LOG, "a") as fh:
                fh.write(json.dumps(r) + "\n")
    
    # Save current state
    save_state(results)
    
    # Send webhook alerts
    if alerts:
        asyncio.run(send_webhook_alert(alerts))
        log_alerts(alerts)
    
    # Write report
    with open(REPORT_PATH, "w") as fh:
        json.dump(results, fh, indent=2)
    
    dead = [r for r in results if r["status"] == "DEAD"]
    runtime_only = [r for r in results if r["status"] == "RUNTIME_ONLY"]
    missing = [r for r in results if r["status"] == "MISSING"]
    ok = [r for r in results if r["status"] == "OK"]
    warn = [r for r in results if r["status"].startswith("WARN")]
    
    print(f"[api-health] OK={len(ok)} DEAD={len(dead)} RUNTIME_ONLY={len(runtime_only)} MISSING={len(missing)} WARN={len(warn)}")
    if dead:
        print("  DEAD: " + ", ".join(r["name"] for r in dead))
    if runtime_only:
        print("  RUNTIME_ONLY: " + ", ".join(r["name"] for r in runtime_only))
    if missing:
        print("  MISSING: " + ", ".join(r["name"] for r in missing))
    if warn:
        print("  WARN: " + ", ".join(r["name"] for r in warn))
    if alerts:
        print(f"  🚨 {len(alerts)} state-change alert(s) sent")
    
    return len(dead) + len(missing)

if __name__ == "__main__":
    sys.exit(main())
