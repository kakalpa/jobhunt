#!/usr/bin/env python3
"""
telegram_notifier.py - Telegram Alert Integration for Job Hunt Command Center
Sends instant notifications for:
- Discovered high-match job opportunities (>= threshold, default 80%)
- Generated application packages (with CV & Cover Letter links)
- Interview status transitions & preparation alerts
- Daily / scheduled scan summaries
"""

import os
import re
import json
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", Path(__file__).resolve().parent.parent)).resolve()

def get_env_variable(var_name: str, default: str = "") -> str:
    """Retrieve variable from environment or .env file."""
    val = os.environ.get(var_name, "").strip()
    if val:
        return val
    env_path = WORKSPACE_DIR / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{var_name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return default

def get_telegram_config() -> dict:
    """Load Telegram configuration."""
    bot_token = get_env_variable("TELEGRAM_BOT_TOKEN")
    chat_id = get_env_variable("TELEGRAM_CHAT_ID")
    min_score = int(get_env_variable("TELEGRAM_MIN_MATCH_SCORE", "80") or "80")
    enabled = bool(bot_token and chat_id)
    return {
        "enabled": enabled,
        "bot_token": bot_token,
        "chat_id": chat_id,
        "min_score": min_score
    }

def send_telegram_message(text: str, parse_mode: str = "HTML", disable_web_page_preview: bool = False) -> bool:
    """Sends a text message via Telegram Bot API using standard library urllib."""
    config = get_telegram_config()
    if not config["enabled"]:
        return False

    url = f"https://api.telegram.org/bot{config['bot_token']}/sendMessage"
    payload = {
        "chat_id": config["chat_id"],
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("ok", False)
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        print(f"⚠️ Telegram API error (HTTP {e.code}): {err_msg}")
        return False
    except Exception as e:
        print(f"⚠️ Telegram network error: {e}")
        return False

def test_telegram_connection() -> dict:
    """Tests the Telegram Bot connection and sends a test message."""
    config = get_telegram_config()
    if not config["bot_token"]:
        return {"success": False, "message": "TELEGRAM_BOT_TOKEN is not configured."}
    if not config["chat_id"]:
        return {"success": False, "message": "TELEGRAM_CHAT_ID is not configured."}

    test_text = (
        "🤖 <b>Job Hunt Command Center</b>\n\n"
        "✅ <b>Telegram Alert System Connected!</b>\n"
        f"📅 <i>Time:</i> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🎯 <i>Alert Match Threshold:</i> {config['min_score']}%\n\n"
        "You will receive live alerts for newly discovered high-match roles and application progress."
    )

    success = send_telegram_message(test_text)
    if success:
        return {"success": True, "message": "Test alert sent successfully to Telegram!"}
    else:
        return {"success": False, "message": "Failed to send message. Please verify Bot Token and Chat ID."}

SENT_JOBS_FILE = WORKSPACE_DIR / "telegram_sent_jobs.json"

def get_job_dedup_keys(job: dict) -> list:
    """Generates unique keys (canonical URL and normalized company+title) to deduplicate alerts."""
    keys = []
    url = (job.get("url") or job.get("job_url") or "").strip()
    if url:
        try:
            parsed = urllib.parse.urlparse(url)
            q_params = urllib.parse.parse_qs(parsed.query)
            clean_params = {}
            for vital_key in ["jk", "id", "jobId"]:
                if vital_key in q_params:
                    clean_params[vital_key] = q_params[vital_key][0]
            query_str = f"?{urllib.parse.urlencode(clean_params)}" if clean_params else ""
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}{query_str}".lower()
            keys.append(clean_url)
        except Exception:
            keys.append(url.split("?")[0].rstrip("/").lower())

    # Use canonical normalization from job_filters for cross-site deduplication
    try:
        from scripts.job_filters import normalize_company, normalize_title
    except ImportError:
        try:
            from job_filters import normalize_company, normalize_title
        except ImportError:
            normalize_company = lambda c: re.sub(r'[^a-zA-Z0-9]', '', str(c).lower())
            normalize_title = lambda t: re.sub(r'[^a-zA-Z0-9]', '', str(t).lower())

    comp_norm = normalize_company(str(job.get("company", "")))
    tit_norm = normalize_title(str(job.get("title", "")))
    if comp_norm and tit_norm:
        keys.append(f"{comp_norm}::{tit_norm}")

    # Also keep raw alphanumeric key for backwards compatibility with existing telegram_sent_jobs.json
    raw_company = re.sub(r'[^a-zA-Z0-9]', '', str(job.get("company", "")).lower())
    raw_title = re.sub(r'[^a-zA-Z0-9]', '', str(job.get("title", "")).lower())
    if raw_company and raw_title:
        keys.append(f"{raw_company}::{raw_title}")

    return keys

def load_sent_job_keys() -> set:
    """Returns set of all job keys that have already been notified or seeded."""
    if SENT_JOBS_FILE.exists():
        try:
            with open(SENT_JOBS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return set(data.keys())
                elif isinstance(data, list):
                    return set(data)
        except Exception as e:
            print(f"⚠️ Notice reading telegram_sent_jobs.json: {e}")
    return set()

def record_sent_job(job: dict):
    """Persists a job's dedup keys to prevent duplicate alerts."""
    keys = get_job_dedup_keys(job)
    if not keys:
        return

    sent_dict = {}
    if SENT_JOBS_FILE.exists():
        try:
            with open(SENT_JOBS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    sent_dict = data
                elif isinstance(data, list):
                    sent_dict = {k: {"sent_at": "initial"} for k in data}
        except Exception:
            sent_dict = {}

    meta = {
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "match_score": job.get("match_score", 0),
        "sent_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    for k in keys:
        sent_dict[k] = meta

    try:
        with open(SENT_JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(sent_dict, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Notice saving telegram_sent_jobs.json: {e}")

def seed_existing_postings(jobs: list):
    """Seeds already known postings into the sent record without dispatching alerts."""
    sent_dict = {}
    if SENT_JOBS_FILE.exists():
        try:
            with open(SENT_JOBS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    sent_dict = data
        except Exception:
            sent_dict = {}

    for j in jobs:
        for k in get_job_dedup_keys(j):
            if k not in sent_dict:
                sent_dict[k] = {
                    "title": j.get("title", ""),
                    "company": j.get("company", ""),
                    "match_score": j.get("match_score", 0),
                    "sent_at": "seeded_prior_scan"
                }

    try:
        with open(SENT_JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(sent_dict, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Notice seeding telegram_sent_jobs.json: {e}")

def notify_new_job_opportunity(job: dict) -> bool:
    """Sends an alert for a high-match scouted job if not previously sent."""
    config = get_telegram_config()
    if not config["enabled"]:
        return False

    score = job.get("match_score", 0)
    if score < config["min_score"]:
        return False

    # Check deduplication keys: NEVER send the same posting twice
    dedup_keys = get_job_dedup_keys(job)
    sent_keys = load_sent_job_keys()
    if any(k in sent_keys for k in dedup_keys):
        return False

    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    location = job.get("location", "Finland")
    platform = job.get("platform", job.get("platform_display", "Web"))
    badge = job.get("language_badge", "🌐 General")
    url = job.get("url", "")
    snippet = job.get("description_snippet", "")
    if len(snippet) > 200:
        snippet = snippet[:197] + "..."

    msg = (
        f"🎯 <b>New High-Match IT Role Scouted! ({score}%)</b>\n\n"
        f"💼 <b>Role:</b> {title}\n"
        f"🏢 <b>Company:</b> {company}\n"
        f"📍 <b>Location:</b> {location}\n"
        f"🌐 <b>Platform:</b> {platform}\n"
        f"🗣️ <b>Language:</b> {badge}\n"
    )
    if snippet and snippet != "N/A":
        msg += f"\n📝 <i>{snippet}</i>\n"
    if url:
        msg += f"\n🔗 <a href=\"{url}\">Open Posting & Apply</a>"

    success = send_telegram_message(msg)
    if success:
        record_sent_job(job)
    return success

def notify_package_generated(company: str, title: str, folder_name: str, has_ai: bool = True) -> bool:
    """Notifies when a complete 9-file application package has been generated."""
    config = get_telegram_config()
    if not config["enabled"]:
        return False

    ai_badge = "✅ Gemini AI Tailored" if has_ai else "⚡ Template Mode"
    msg = (
        "🚀 <b>Application Package Ready!</b>\n\n"
        f"🏢 <b>Company:</b> {company}\n"
        f"💼 <b>Position:</b> {title}\n"
        f"📁 <b>Directory:</b> <code>{folder_name}</code>\n"
        f"✨ <b>Engine:</b> {ai_badge}\n\n"
        "📄 <b>Generated Assets:</b>\n"
        " • Tailored CV (A4 PDF & Markdown)\n"
        " • 3-Pillar Cover Letter (A4 PDF & Markdown)\n"
        " • ATS Keyword Match & Gap Report\n"
        " • Screening Form Answers & Lisätietoja Script\n"
        " • Interview Preparation Guide"
    )
    return send_telegram_message(msg)

def notify_status_update(company: str, title: str, new_status: str) -> bool:
    """Notifies when a job moves to a new pipeline stage."""
    config = get_telegram_config()
    if not config["enabled"]:
        return False

    icon = "📋"
    extra = ""
    if new_status == "Interviewing":
        icon = "🎉"
        extra = "\n\n💡 <i>Interview preparation guide and STAR talking points are available in your package!</i>"
    elif new_status == "Applied":
        icon = "📤"
    elif new_status == "Offer":
        icon = "🏆"
        extra = "\n\n🍾 <i>Congratulations! Check salary benchmark guides in your dashboard.</i>"
    elif new_status == "Rejected":
        icon = "📫"

    msg = (
        f"{icon} <b>Pipeline Status Update: {new_status}</b>\n\n"
        f"🏢 <b>Company:</b> {company}\n"
        f"💼 <b>Role:</b> {title}\n"
        f"📊 <b>New Status:</b> <b>{new_status}</b>{extra}"
    )
    return send_telegram_message(msg)

def notify_scout_scan_summary(new_roles_count: int, high_match_count: int, total_unique: int) -> bool:
    """Summary notification after a scheduled or background scout scan."""
    config = get_telegram_config()
    if not config["enabled"] or new_roles_count == 0:
        return False

    msg = (
        "📡 <b>Job Scout Scan Completed</b>\n\n"
        f"🔍 <b>Unique IT Roles Active:</b> {total_unique}\n"
        f"⭐ <b>New Opportunities Discovered:</b> {new_roles_count}\n"
        f"🎯 <b>High Match (≥ 80%):</b> {high_match_count}\n\n"
        "Open your Job Hunt Command Center dashboard to review and generate packages."
    )
    return send_telegram_message(msg)

API_ERROR_NOTIFIED_FILE = WORKSPACE_DIR / ".api_error_notified.json"

def notify_api_key_error(service_name: str, error_details: str, cooldown_hours: float = 4.0) -> bool:
    """
    Sends a high-priority Telegram alert when an AI or cloud API key encounters errors (e.g. 401 Unauthorized).
    Rate-limited by cooldown_hours for identical errors to avoid notification spam.
    """
    config = get_telegram_config()
    if not config["enabled"]:
        return False

    now = datetime.now()
    now_iso = now.isoformat()
    
    # Check rate limit cache
    cache = {}
    if API_ERROR_NOTIFIED_FILE.exists():
        try:
            cache = json.loads(API_ERROR_NOTIFIED_FILE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    last_sent_str = cache.get(service_name, {}).get("timestamp", "")
    last_err = cache.get(service_name, {}).get("error", "")

    if last_sent_str and last_err == str(error_details):
        try:
            last_dt = datetime.fromisoformat(last_sent_str)
            hours_elapsed = (now - last_dt).total_seconds() / 3600.0
            if hours_elapsed < cooldown_hours:
                return False  # Suppressed by rate limit
        except Exception:
            pass

    msg = (
        "⚠️ <b>Job Hunt Command Center Alert</b>\n\n"
        "🔴 <b>AI Service Authentication Error</b>\n"
        f"<b>Service:</b> {service_name}\n"
        f"<b>Error:</b> <code>{error_details}</code>\n"
        "<b>Status:</b> Live AI tailoring is paused; application packages will automatically use calibrated offline archetype templates.\n\n"
        "🔧 <b>Action:</b> Update your <code>GEMINI_API_KEY</code> in the dashboard Settings modal or <code>.env</code> file."
    )

    success = send_telegram_message(msg)
    if success:
        cache[service_name] = {
            "timestamp": now_iso,
            "error": str(error_details)
        }
        try:
            API_ERROR_NOTIFIED_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except Exception:
            pass
    return success

if __name__ == "__main__":
    import sys
    print("Testing Telegram Notifier...")
    res = test_telegram_connection()
    print(res)
