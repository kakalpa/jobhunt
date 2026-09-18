#!/usr/bin/env python3
"""
retention_manager.py - Posting Retention & Storage Cleanup Engine
Automatically manages disk space by purging files or directories for expired
and inactive job postings based on a configurable retention policy.
"""

import os
import sys
import re
import json
import shutil
import argparse
from datetime import datetime, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", APP_DIR)).resolve()

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(APP_DIR / "scripts"))
if str(WORKSPACE_DIR) != str(APP_DIR):
    sys.path.insert(0, str(WORKSPACE_DIR))
    sys.path.insert(0, str(WORKSPACE_DIR / "scripts"))

# Protected system paths that must NEVER be cleaned
PROTECTED_SYSTEM_DIRS = {
    ".git", ".agents", ".claude", "scripts", "dashboard", "static", "templates",
    "venv", ".venv", "__pycache__", "gpu_sleep_diag", "outliner_ai", "Hunt"
}

PROTECTED_SYSTEM_FILES = {
    "Base_CV.md", "Base_CV.md.backup", "General_CV.md", "APPLICATIONS_TRACKER.md",
    "pipeline_data.json", "JOB_SCOUT_FEED.md", "scout_latest_report.json",
    "telegram_sent_jobs.json", "WORKFLOW_DIAGRAM.md", "summary_response.md",
    "requirements.txt", "Dockerfile", "docker-compose.yml", "docker-entrypoint.sh",
    "start_dashboard.sh", ".env", ".env.example", ".dockerignore", "skills-lock.json"
}

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

def get_retention_config() -> dict:
    """Load retention policy configuration."""
    days_str = get_env_variable("POSTING_RETENTION_DAYS", "30")
    try:
        days = int(days_str)
    except ValueError:
        days = 30

    protect_statuses_str = get_env_variable("RETENTION_PROTECT_STATUS", "interviewing,offer,applied")
    protect_statuses = {s.strip().lower() for s in protect_statuses_str.split(",") if s.strip()}
    
    mode = get_env_variable("RETENTION_MODE", "full").lower()
    if mode not in ["full", "pdfs_only"]:
        mode = "full"

    notify_telegram = get_env_variable("RETENTION_NOTIFY_TELEGRAM", "true").lower() in ["true", "1", "yes"]

    return {
        "days": days,
        "protect_statuses": protect_statuses,
        "mode": mode,
        "notify_telegram": notify_telegram,
        "enabled": days > 0
    }

def load_pipeline_data() -> dict:
    """Load pipeline database."""
    db_file = WORKSPACE_DIR / "pipeline_data.json"
    if db_file.exists():
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_pipeline_data(data: dict):
    """Save pipeline database."""
    db_file = WORKSPACE_DIR / "pipeline_data.json"
    try:
        with open(db_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Error saving pipeline data: {e}")

def get_directory_size(path: Path) -> int:
    """Calculate total size in bytes of a directory."""
    total = 0
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return total

def is_application_folder(folder_path: Path) -> bool:
    """Checks whether a folder is an application package directory."""
    if not folder_path.is_dir() or folder_path.name in PROTECTED_SYSTEM_DIRS or folder_path.name.startswith("."):
        return False

    # Check for signature files that mark a job application package
    has_cv = any("Cover_Letter" not in f.name and "Job_Description" not in f.name and "Analysis" not in f.name and f.name.endswith(".md") for f in folder_path.glob("*.md"))
    has_jd = any(folder_path.glob("*Job_Description*.md")) or any(folder_path.glob("*Analysis*.md"))
    has_cl = any(folder_path.glob("*Cover_Letter*.md"))

    return has_cv or has_jd or has_cl

def scan_retention_candidates(days: int = None, protect_statuses: set = None) -> dict:
    """
    Scans the workspace for application packages older than the retention threshold.
    Returns details on candidates, sizes, and protected items.
    """
    config = get_retention_config()
    days = days if days is not None else config["days"]
    protect_statuses = protect_statuses if protect_statuses is not None else config["protect_statuses"]
    
    cutoff_datetime = datetime.now() - timedelta(days=days)
    pipeline_db = load_pipeline_data()

    candidates = []
    protected = []
    total_reclaimable_bytes = 0

    if not WORKSPACE_DIR.exists():
        return {
            "days": days,
            "cutoff_date": cutoff_datetime.strftime("%Y-%m-%d"),
            "candidates": [],
            "protected": [],
            "total_bytes": 0,
            "total_mb": 0.0
        }

    for item in WORKSPACE_DIR.iterdir():
        if not item.is_dir():
            continue
        if item.name in PROTECTED_SYSTEM_DIRS or item.name.startswith("."):
            continue

        if not is_application_folder(item):
            continue

        folder_name = item.name
        db_record = pipeline_db.get(folder_name, {})

        # Check explicit pinning/protection
        if db_record.get("pinned", False) or db_record.get("keep", False):
            protected.append({
                "folder": folder_name,
                "reason": "Explicitly Pinned",
                "status": db_record.get("status", "unknown")
            })
            continue

        # Check status protection (e.g. interviewing, offer, applied)
        current_status = db_record.get("status", "ready").lower().strip()
        if current_status in protect_statuses:
            protected.append({
                "folder": folder_name,
                "reason": f"Protected Status ({current_status.title()})",
                "status": current_status
            })
            continue

        # Determine age of posting package
        # Priority: 1. db created_at/applied_date, 2. folder latest mtime, 3. folder ctime
        folder_time = None
        if db_record.get("applied_date"):
            try:
                folder_time = datetime.strptime(db_record["applied_date"], "%Y-%m-%d")
            except Exception:
                pass
        
        if not folder_time:
            try:
                # Find newest file in the folder to accurately gauge activity
                latest_mtime = max((f.stat().st_mtime for f in item.rglob("*") if f.is_file()), default=item.stat().st_mtime)
                folder_time = datetime.fromtimestamp(latest_mtime)
            except Exception:
                folder_time = datetime.fromtimestamp(item.stat().st_mtime)

        age_days = (datetime.now() - folder_time).days
        folder_size = get_directory_size(item)

        if folder_time < cutoff_datetime:
            candidates.append({
                "folder": folder_name,
                "path": str(item),
                "age_days": age_days,
                "last_active": folder_time.strftime("%Y-%m-%d"),
                "status": current_status,
                "size_bytes": folder_size,
                "size_kb": round(folder_size / 1024, 1),
                "pdf_count": len(list(item.glob("*.pdf")))
            })
            total_reclaimable_bytes += folder_size
        else:
            protected.append({
                "folder": folder_name,
                "reason": f"Within retention window ({age_days}d < {days}d)",
                "status": current_status
            })

    return {
        "days": days,
        "cutoff_date": cutoff_datetime.strftime("%Y-%m-%d"),
        "candidates": candidates,
        "protected": protected,
        "total_bytes": total_reclaimable_bytes,
        "total_mb": round(total_reclaimable_bytes / (1024 * 1024), 2)
    }

def clean_expired_postings(
    days: int = None,
    protect_statuses: set = None,
    mode: str = "full",
    dry_run: bool = False
) -> dict:
    """
    Executes retention cleanup of expired job postings.
    Modes:
      - 'full': Removes the entire application package folder.
      - 'pdfs_only': Removes generated *.pdf files inside the folder, keeping markdown.
    """
    scan_result = scan_retention_candidates(days=days, protect_statuses=protect_statuses)
    candidates = scan_result["candidates"]

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "mode": mode,
            "removed_count": len(candidates),
            "freed_bytes": scan_result["total_bytes"],
            "freed_mb": scan_result["total_mb"],
            "details": candidates
        }

    pipeline_db = load_pipeline_data()
    removed_items = []
    freed_bytes = 0

    for c in candidates:
        folder_path = Path(c["path"])
        if not folder_path.exists():
            continue

        try:
            if mode == "full":
                folder_bytes = get_directory_size(folder_path)
                shutil.rmtree(folder_path)
                freed_bytes += folder_bytes
                removed_items.append({
                    "folder": c["folder"],
                    "freed_kb": round(folder_bytes / 1024, 1),
                    "action": "folder_deleted"
                })

                # Update pipeline metadata to note cleanup rather than orphaned state
                if c["folder"] in pipeline_db:
                    pipeline_db[c["folder"]]["cleaned_up"] = True
                    pipeline_db[c["folder"]]["cleaned_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            elif mode == "pdfs_only":
                folder_freed = 0
                for pdf_file in folder_path.glob("*.pdf"):
                    sz = pdf_file.stat().st_size
                    pdf_file.unlink()
                    folder_freed += sz
                freed_bytes += folder_freed
                removed_items.append({
                    "folder": c["folder"],
                    "freed_kb": round(folder_freed / 1024, 1),
                    "action": "pdfs_purged"
                })

        except Exception as e:
            print(f"⚠️ Error cleaning folder {folder_path}: {e}")

    save_pipeline_data(pipeline_db)

    # Optional Telegram alert if items were cleaned
    if removed_items and get_retention_config()["notify_telegram"]:
        try:
            from scripts.telegram_notifier import send_telegram_message
            freed_mb = round(freed_bytes / (1024 * 1024), 2)
            msg = (
                f"🧹 <b>Storage Retention Cleanup Completed</b>\n\n"
                f"📦 <b>Cleaned Postings:</b> {len(removed_items)} packages (> {scan_result['days']} days old)\n"
                f"💾 <b>Disk Space Reclaimed:</b> {freed_mb} MB\n"
                f"⚙️ <b>Mode:</b> {mode.upper()}\n\n"
                f"<i>Protected active stages (Interviewing/Offer) were preserved.</i>"
            )
            send_telegram_message(msg)
        except Exception:
            pass

    return {
        "success": True,
        "dry_run": False,
        "mode": mode,
        "removed_count": len(removed_items),
        "freed_bytes": freed_bytes,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "details": removed_items
    }

def main():
    parser = argparse.ArgumentParser(description="Clean expired job postings based on retention threshold.")
    parser.add_argument("--days", type=int, default=None, help="Retention period in days (default from .env or 30)")
    parser.add_argument("--mode", choices=["full", "pdfs_only"], default="full", help="Cleanup mode")
    parser.add_argument("--dry-run", action="store_true", help="Preview deletions without deleting anything")
    parser.add_argument("--protect", nargs="+", default=None, help="Statuses to protect (e.g. interviewing offer)")
    
    args = parser.parse_args()
    
    protect_set = set(args.protect) if args.protect else None
    res = clean_expired_postings(
        days=args.days,
        protect_statuses=protect_set,
        mode=args.mode,
        dry_run=args.dry_run
    )
    
    prefix = "[DRY-RUN] " if args.dry_run else ""
    print(f"\n{prefix}Retention Cleanup Summary:")
    print(f"  • Expired Postings Identified: {res['removed_count']}")
    print(f"  • Space Reclaimed: {res['freed_mb']} MB ({res['freed_bytes']} bytes)")
    print(f"  • Mode: {res['mode']}")
    if res.get("details"):
        print("\nAffected folders:")
        for item in res["details"][:10]:
            print(f"  - {item['folder']} ({item.get('size_kb') or item.get('freed_kb')} KB)")
        if len(res["details"]) > 10:
            print(f"  ... and {len(res['details']) - 10} more")

if __name__ == "__main__":
    main()
