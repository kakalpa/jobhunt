#!/usr/bin/env python3
"""
Job Hunter Dashboard - Local Pipeline Management GUI
Flask backend for tracking job applications across the entire lifecycle:
Scouted -> Ready to Apply -> Applied -> Interviewing -> Offer / Rejected.
Now includes IT-only domain filtering, Finnish language requirement tagging,
and cross-site deduplication.
"""

import os
import sys
import re
import json
import glob
import io
import zipfile
import subprocess
from datetime import datetime
from pathlib import Path
import secrets
from flask import (
    Flask, render_template, jsonify, request, send_file,
    send_from_directory, abort, session, redirect, url_for
)

# Set up imports for scripts.job_filters
APP_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", APP_DIR)).resolve()

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(APP_DIR / "scripts"))
if str(WORKSPACE_DIR) != str(APP_DIR):
    sys.path.insert(0, str(WORKSPACE_DIR))
    sys.path.insert(0, str(WORKSPACE_DIR / "scripts"))

try:
    from scripts.auth_manager import (
        load_auth_config, save_auth_config, verify_credentials,
        check_rate_limit, record_failed_attempt, reset_rate_limit,
        generate_csrf_token, validate_csrf_token, generate_totp_secret,
        get_totp_uri, verify_totp, consume_recovery_code,
        generate_qr_svg, generate_recovery_codes
    )
except ImportError:
    try:
        from auth_manager import (
            load_auth_config, save_auth_config, verify_credentials,
            check_rate_limit, record_failed_attempt, reset_rate_limit,
            generate_csrf_token, validate_csrf_token, generate_totp_secret,
            get_totp_uri, verify_totp, consume_recovery_code,
            generate_qr_svg, generate_recovery_codes
        )
    except ImportError:
        load_auth_config = lambda: {"auth_enabled": False, "username": "admin", "password_hash": "", "mfa_enabled": False}
        save_auth_config = lambda c: None
        verify_credentials = lambda u, p, c: True
        check_rate_limit = lambda ip: (True, 0)
        record_failed_attempt = lambda ip: 5
        reset_rate_limit = lambda ip: None
        generate_csrf_token = lambda s, k: ""
        validate_csrf_token = lambda t, s, k: True
        generate_totp_secret = lambda: ""
        get_totp_uri = lambda u, s: ""
        verify_totp = lambda s, t: True
        consume_recovery_code = lambda c, cfg: False
        generate_qr_svg = lambda u: None
        generate_recovery_codes = lambda n=8: []

from scripts.job_filters import (
    is_it_job,
    detect_language_requirement,
    normalize_company,
    normalize_title,
    deduplicate_job_records,
    get_candidate_contact_info
)
from scripts.generate_package import generate_application_package, generate_interview_prep

try:
    from scripts.telegram_notifier import (
        get_telegram_config,
        test_telegram_connection,
        notify_package_generated,
        notify_status_update,
        notify_new_job_opportunity
    )
except ImportError:
    try:
        from telegram_notifier import (
            get_telegram_config,
            test_telegram_connection,
            notify_package_generated,
            notify_status_update,
            notify_new_job_opportunity
        )
    except ImportError:
        get_telegram_config = lambda: {"enabled": False, "bot_token": "", "chat_id": "", "min_score": 80}
        test_telegram_connection = lambda: {"success": False, "message": "Telegram module unavailable"}
        notify_package_generated = lambda *args, **kwargs: False
        notify_status_update = lambda *args, **kwargs: False
        notify_new_job_opportunity = lambda *args, **kwargs: False

try:
    from scripts.retention_manager import (
        get_retention_config,
        scan_retention_candidates,
        clean_expired_postings
    )
except ImportError:
    try:
        from retention_manager import (
            get_retention_config,
            scan_retention_candidates,
            clean_expired_postings
        )
    except ImportError:
        get_retention_config = lambda: {"days": 30, "protect_statuses": {"interviewing", "offer", "applied"}, "mode": "full", "enabled": True}
        scan_retention_candidates = lambda **kwargs: {"candidates": [], "protected": [], "total_mb": 0.0}
        clean_expired_postings = lambda **kwargs: {"success": False, "removed_count": 0, "freed_mb": 0.0}

template_dir = WORKSPACE_DIR / "dashboard" / "templates"
if not template_dir.exists():
    template_dir = Path(__file__).resolve().parent / "templates"

static_dir = WORKSPACE_DIR / "dashboard" / "static"
if not static_dir.exists():
    static_dir = Path(__file__).resolve().parent / "static"

app = Flask(__name__, template_folder=str(template_dir), static_folder=str(static_dir))

AUTH_CONFIG = load_auth_config()
app.secret_key = AUTH_CONFIG.get("secret_key") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 7  # 7 days

def get_client_ip() -> str:
    """Extract real client IP behind reverse proxy or direct."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"

def is_client_authenticated() -> bool:
    global AUTH_CONFIG
    AUTH_CONFIG = load_auth_config()
    if not AUTH_CONFIG.get("auth_enabled"):
        return True
    
    if session.get("authenticated") and session.get("user") == AUTH_CONFIG.get("username", "admin"):
        if AUTH_CONFIG.get("mfa_enabled"):
            return session.get("mfa_verified") is True
        return True
    return False

def get_or_create_csrf_token() -> str:
    if "csrf_session_id" not in session:
        session["csrf_session_id"] = secrets.token_hex(16)
    return generate_csrf_token(session["csrf_session_id"], app.secret_key)

@app.before_request
def security_and_auth_guard():
    global AUTH_CONFIG
    AUTH_CONFIG = load_auth_config()

    path = request.path
    # Public exemptions
    if (
        path.startswith("/static/") or
        path in ("/login", "/login/verify-mfa", "/login/setup-mfa", "/logout", "/healthz")
    ):
        return None

    # Enforce authentication if enabled
    if not is_client_authenticated():
        if path.startswith("/api/"):
            return jsonify({"error": "Unauthorized. Please authenticate."}), 401
        return redirect(f"/login?next={path}")

    # Enforce CSRF protection on mutation requests if auth is enabled
    if AUTH_CONFIG.get("auth_enabled") and request.method in ("POST", "PUT", "DELETE", "PATCH"):
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        session_id = session.get("csrf_session_id", "")
        if not token or not validate_csrf_token(token, session_id, app.secret_key):
            if path.startswith("/api/"):
                return jsonify({"error": "Forbidden: Invalid or expired CSRF token."}), 403
            return render_template("login.html", error="Session expired or invalid security token. Please log in again.", stage="credentials"), 403

@app.after_request
def apply_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' https: data: 'unsafe-inline' 'unsafe-eval'; "
        "img-src 'self' data: https: blob:; "
        "font-src 'self' https: data:; "
        "connect-src 'self' https:;"
    )
    return response

PIPELINE_DB = WORKSPACE_DIR / "pipeline_data.json"
JOB_FEED_FILE = WORKSPACE_DIR / "JOB_SCOUT_FEED.md"
TRACKER_FILE = WORKSPACE_DIR / "APPLICATIONS_TRACKER.md"
SCOUT_REPORT_FILE = WORKSPACE_DIR / "scout_latest_report.json"

def load_latest_scout_report() -> dict:
    if SCOUT_REPORT_FILE.exists():
        try:
            with open(SCOUT_REPORT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

scout_process_status = {
    "is_running": False,
    "mode": "idle",
    "stage": "Ready to scan",
    "progress_percent": 0,
    "logs": [],
    "last_run": None,
    "error": None,
    "report": load_latest_scout_report()
}

def load_pipeline_data() -> dict:
    if PIPELINE_DB.exists():
        try:
            with open(PIPELINE_DB, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_pipeline_data(data: dict):
    with open(PIPELINE_DB, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def extract_match_score(folder_path: Path) -> int:
    """Extract calculated match score from Job Analysis or ATS report."""
    for report in folder_path.glob("*Analysis*.md"):
        try:
            content = report.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'(?:Match Score|Calculated Match Score|Score)[^0-9\n]*(\d{2,3})%?', content, re.I)
            if m:
                score = int(m.group(1))
                if 50 <= score <= 100:
                    return score
        except Exception:
            pass
    for report in folder_path.glob("*ATS*.md"):
        try:
            content = report.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'(?:Overall Match Score|Current score|Match Score|Compatibility Score|Match)[^0-9\n]*(\d{2,3})%?', content, re.I)
            if m:
                score = int(m.group(1))
                if 50 <= score <= 100:
                    return score
        except Exception:
            pass
    return 85

def extract_ats_score(folder_path: Path) -> int | None:
    """Extract ATS Compatibility score from ATS report."""
    for report in folder_path.glob("*ATS*.md"):
        try:
            content = report.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'(?:Overall ATS Compatibility Score|Compatibility Score|Overall Match Score|Current score|ATS Score|Score)[^0-9\n]*(\d{2,3})(?:/100|%)?', content, re.I)
            if m:
                score = int(m.group(1))
                if 50 <= score <= 100:
                    return score
        except Exception:
            pass
    return None
KNOWN_JOB_URLS = {
    "Titanium_IT_Support_Specialist": "https://www.linkedin.com/jobs/view/4463010586",
    "Gapit_Nordics_Operations_Engineer": "https://www.linkedin.com/jobs/view/4467069716",
    "Iron_Systems_Onsite_Support_Engineer": "https://www.linkedin.com/jobs/view/4462851624",
    "NVIDIA_Solutions_Architect_Graduate": "https://www.linkedin.com/jobs/view/4447669568",
    "Siemens_Healthineers_SW_Field_Service_Engineer": "https://www.linkedin.com/jobs/view/4448219586",
    "Planmeca_Junior_Project_Specialist_ERP": "https://fi.indeed.com/viewjob?jk=aa32580fad5080a7",
    "GE_Vernova_Lead_Cyber_Security_Engineer": "https://www.linkedin.com/jobs/view/4437814771",
    "Milestone_Senior_Data_Center_Operations_Engineer": "https://www.linkedin.com/jobs/view/4445553507",
    "DNV_Senior_Security_Consultant": "https://www.linkedin.com/jobs/view/4464233458",
    "Odevo_IT_Infrastructure_Engineer": "https://www.linkedin.com/jobs/view/4464862626",
    "Elisa_Senior_Cyber_Security_Analyst": "https://www.linkedin.com/jobs/view/4465764253",
    "Hoxhunt_Junior_Security_Engineer_GRC": "https://www.linkedin.com/jobs/view/4460987834",
    "NSC_Global_Field_Service_Technician": "https://www.linkedin.com/jobs/view/4444432686",
    "IT_Support_Specialist_Verda": "https://www.linkedin.com/jobs/view/4445726830",
    "Qt_Group_Senior_Security_Specialist": "https://www.linkedin.com/jobs/view/4463859582",
    "UpCloud_SRE": "https://www.linkedin.com/jobs/view/4461963586",
    "Istekki_ITSM_Solutions_Specialist": "https://www.istekki.fi/uratarinat/",
    "Kaukora_Systems_Expert": "https://www.kaukora.fi/",
    "Nebius_L1_IT_Datacenter_Technician": "https://nebius.com/careers",
    "Nebius_Datacenter_L2_IT_Technician": "https://nebius.com/careers",
    "Nebius_IT_Infrastructure_Engineer": "https://nebius.com/careers",
    "Quest_Global_Cyber_Security_Engineer": "https://www.quest-global.com/careers/",
    "Jobgether_Tier_1_Support_Smart_Cooler_Vending": "https://jobgether.com/",
    "Clear_Guidance_Partners_Systems_Administrator": "https://clear-guidance.com/careers/",
    "Cognizant_Windows_Administrator": "https://careers.cognizant.com/",
    "RELEX_Senior_IT_Security_Engineer": "https://www.relexsolutions.com/careers/",
    "NetNordic_Security_Analyst": "https://netnordic.fi/ura-meilla/",
    "ALTEN_Finland_IT_Support_Engineer": "https://www.alten.fi/ura/",
    "Iron_Systems_OSS_Engineer": "https://ironsystems.com/careers",
    "HCLTech_L1_Service_Desk_Analyst": "https://www.hcltech.com/careers",
    "Microsoft_Data_Center_Technician": "https://careers.microsoft.com/",
    "TikTok_Data_Center_Operations_Engineer": "https://careers.tiktok.com/",
    "IQM_Technical_Support_Engineer": "https://www.meetiqm.com/careers/",
    "Technical_Support_Engineer_IQM": "https://www.meetiqm.com/careers/",
    "Senior_System_Administrator_IQM": "https://www.meetiqm.com/careers/",
    "ReOrbit_IT_Administrator": "https://www.reorbit.space/careers",
    "Skylo_IT_Operations_Specialist": "https://www.skylo.tech/careers",
    "Field_Service_Engineer_GlobalBlue": "https://www.globalblue.com/corporate/careers",
    "OSS_Engineer_Infosys": "https://www.infosys.com/careers/",
    "PHZfi_DevOps_Sysadmin": "https://phz.fi/rekry/",
    "IT_Security_Operations_Specialist_NestAI": "https://www.linkedin.com/jobs/search/?keywords=NestAI+Security",
    "Cybersecurity_IT_Specialist_Splunk_EU_Remote": "https://www.linkedin.com/jobs/search/?keywords=Cybersecurity+IT+Specialist+Splunk",
    "Data_Center_Infrastructure_Project_Delivery_Expert": "https://www.linkedin.com/jobs/search/?keywords=Data+Center+Infrastructure+Delivery+Expert+Finland",
    "Security_Operations_Analyst_AI_Training": "https://www.linkedin.com/jobs/search/?keywords=Security+Operations+Analyst+AI+Finland",
    "outliner_ai": "https://outliner.ai/",
}

def is_candidate_personal_url(u: str) -> bool:
    """Return True if URL belongs to the candidate rather than the job posting."""
    if not u:
        return True
    u_lower = u.lower()
    cand = get_candidate_contact_info(WORKSPACE_DIR)
    bad_tokens = ["linkedin.com/in/", "github.com/"]
    for val in [cand.get("email"), cand.get("linkedin"), cand.get("github")]:
        if val:
            bad_tokens.append(val.lower())
    return any(bad in u_lower for bad in bad_tokens if bad)

def extract_role_info_from_jd(folder_path: Path, folder_name: str) -> dict:
    """Extract role title, company, location, and URL from JD or Analysis files."""
    role = folder_name.replace("_", " ")
    company = "Company"
    location = "Finland"
    url = KNOWN_JOB_URLS.get(folder_name, "")
    
    jd_files = list(folder_path.glob("*Job_Description*.md")) + list(folder_path.glob("*Analysis*.md"))
    for jd_file in jd_files:
        try:
            content = jd_file.read_text(encoding="utf-8", errors="ignore")
            # Company
            if company == "Company":
                m_comp = re.search(r'\*\*Company:\*\*\s*(.+)', content)
                if m_comp:
                    company = m_comp.group(1).strip()
            # Title
            if role == folder_name.replace("_", " "):
                m_tit = re.search(r'\*\*Title:\*\*\s*(.+)', content)
                if m_tit:
                    role = m_tit.group(1).strip()
                else:
                    m_title = re.search(r'^#\s*(?:Job Description:\s*|JOB ANALYSIS REPORT\s*#?\s*)?(.+?)(?:—|-|at|\n)', content, re.M)
                    if m_title and len(m_title.group(1).strip()) > 3:
                        role = m_title.group(1).strip()
            # Location
            if location == "Finland":
                m_loc = re.search(r'\*\*Location:\*\*\s*(.+)', content)
                if m_loc:
                    location = m_loc.group(1).strip()
            # URL regex: handles **Job Posting URL:**, * **URL:**, markdown links [text](http...), etc.
            m_url = re.search(r'(?:Job Posting URL|Direct Link|URL|Apply Link|Posting Link|Application Link|Job Link|Apply|Reference)[\s\*:]*[:\-]?[\s\*]*(?:\[.*?\]\()?([https?://[^\s\)\"\]\>]+)', content, re.I)
            if m_url:
                found_url = m_url.group(1).strip().rstrip('.,;*)"\'')
                if not is_candidate_personal_url(found_url):
                    url = found_url
                    break
        except Exception:
            pass
            
    # Fallback to deduce company from folder name
    if company == "Company":
        parts = folder_name.split("_")
        if len(parts) > 1:
            company = parts[0]
            role = " ".join(parts[1:])
            
    # Fallback search URL if none found
    if not url:
        safe_kw = f"{role} {company}".replace(" ", "+")
        url = f"https://www.linkedin.com/jobs/search/?keywords={safe_kw}&location=Finland"
            
    return {
        "title": role,
        "company": company,
        "location": location,
        "url": url
    }

def scan_prepared_applications() -> list:
    """Scan directory tree for all prepared job application packages."""
    applications = []
    saved_db = load_pipeline_data()
    
    # Exclude system folders
    excluded_dirs = {'.git', '.claude', '.gemini', 'scripts', 'dashboard', 'static', 'templates', 'venv', '__pycache__', 'Hunt', 'gpu_sleep_diag'}
    
    for item in WORKSPACE_DIR.iterdir():
        if not item.is_dir() or item.name in excluded_dirs or item.name.startswith('.'):
            continue
            
        # Check if contains prepared artifacts
        has_cv = any(item.glob("*CV*.md")) or any(item.glob("*.pdf")) or any(item.glob("*Cover_Letter*.md"))
        has_jd = any(item.glob("*Job_Description*.md")) or any(item.glob("*Analysis*.md"))
        
        if not (has_cv or has_jd):
            continue
            
        folder_id = item.name
        role_info = extract_role_info_from_jd(item, item.name)
        match_score = extract_match_score(item)
        ats_score = extract_ats_score(item)
        
        # Files discovery
        cv_pdfs = [f.name for f in item.glob("*.pdf") if "Cover_Letter" not in f.name]
        cv_pdf = cv_pdfs[0] if cv_pdfs else None
        
        cl_pdfs = [f.name for f in item.glob("*Cover_Letter*.pdf")]
        cl_pdf = cl_pdfs[0] if cl_pdfs else None
        
        cl_mds = [f.name for f in item.glob("*Cover_Letter*.md")]
        cl_md = cl_mds[0] if cl_mds else None
        
        qa_mds = [f.name for f in item.glob("*Application_Form_Answers*.md")]
        qa_md = qa_mds[0] if qa_mds else None
        
        prep_mds = [f.name for f in item.glob("*Interview_Prep*.md")]
        prep_md = prep_mds[0] if prep_mds else None
        
        ats_mds = [f.name for f in item.glob("*ATS*.md")]
        ats_md = ats_mds[0] if ats_mds else None
        
        jd_mds = [f.name for f in item.glob("*Job_Description*.md")]
        jd_md = jd_mds[0] if jd_mds else None

        # Detect language requirement from JD
        jd_text = ""
        if jd_md:
            try:
                jd_text = (item / jd_md).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
        lang_info = detect_language_requirement(role_info["title"], jd_text)

        # Saved user state & canonical URL resolution
        db_record = saved_db.get(folder_id, {})
        status = db_record.get("status", "ready")
        applied_date = db_record.get("applied_date", "")
        portal = db_record.get("portal", "Company Portal")
        notes = db_record.get("notes", "")
        interview_date = db_record.get("interview_date", "")
        
        job_url = db_record.get("url") or role_info.get("url") or KNOWN_JOB_URLS.get(folder_id, "")
        
        # Platform display name
        platform_display = "Direct"
        if "linkedin.com" in job_url:
            platform_display = "LinkedIn"
        elif "indeed.com" in job_url:
            platform_display = "Indeed"
        elif "duunitori.fi" in job_url:
            platform_display = "Duunitori"
        elif role_info.get("company") and role_info["company"] != "Company":
            platform_display = f"{role_info['company']} Careers"
        else:
            platform_display = "Company Portal"

        # Follow-up aging calculation
        follow_up_due = False
        days_since_applied = None
        if status == "applied" and applied_date:
            try:
                clean_date = applied_date.split()[0].replace('/', '-')
                if '-' in clean_date:
                    parts = clean_date.split('-')
                    if len(parts[0]) == 4:
                        d = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                    else:
                        d = datetime(int(parts[2]), int(parts[1]), int(parts[0]))
                elif '.' in clean_date:
                    parts = clean_date.split('.')
                    d = datetime(int(parts[2]), int(parts[1]), int(parts[0]))
                else:
                    d = None
                if d:
                    days_since_applied = max(0, (datetime.now() - d).days)
                    if days_since_applied >= 7:
                        follow_up_due = True
            except Exception:
                pass

        applications.append({
            "id": folder_id,
            "folder": folder_id,
            "type": "prepared",
            "title": role_info["title"],
            "company": role_info["company"],
            "location": role_info["location"],
            "url": job_url,
            "match_score": match_score,
            "ats_score": ats_score,
            "status": status,
            "follow_up_due": follow_up_due,
            "days_since_applied": days_since_applied,
            "language_tag": lang_info["tag"],
            "language_badge": lang_info["badge"],
            "language_class": lang_info["class"],
            "platform": platform_display,
            "platform_display": platform_display,
            "platforms": [platform_display],
            "applied_date": applied_date,
            "portal": portal,
            "notes": notes,
            "interview_date": interview_date,
            "has_cv_pdf": bool(cv_pdf),
            "cv_pdf": cv_pdf,
            "has_cl_pdf": bool(cl_pdf),
            "cl_pdf": cl_pdf,
            "has_cl_md": bool(cl_md),
            "cl_md": cl_md,
            "has_qa": bool(qa_md),
            "qa_md": qa_md,
            "has_prep": bool(prep_md),
            "prep_md": prep_md,
            "has_ats": bool(ats_md),
            "ats_md": ats_md,
            "has_jd": bool(jd_md),
            "jd_md": jd_md
        })
        
    return applications

def scan_scouted_feed(prepared_applications: list) -> list:
    """Parse JOB_SCOUT_FEED.md, deduplicate across platforms, and exclude already prepared jobs."""
    scouted_jobs = []
    saved_db = load_pipeline_data()
    
    if not JOB_FEED_FILE.exists():
        return scouted_jobs
        
    content = JOB_FEED_FILE.read_text(encoding="utf-8", errors="ignore")
    
    # Build fast matching set of normalized prepared applications
    prepared_keys = set()
    for p in prepared_applications:
        c_norm = normalize_company(p["company"])
        t_norm = normalize_title(p["title"])
        f_norm = normalize_company(p["folder"])
        if c_norm and t_norm:
            prepared_keys.add(f"{c_norm}_{t_norm}")
        if c_norm and len(c_norm) > 3:
            prepared_keys.add(c_norm)
        if f_norm and len(f_norm) > 3:
            prepared_keys.add(f_norm)

    # 8-column pattern: | Score | Title | Company | Location | Language | Platform | Status | Direct Link |
    row_pattern_8col = re.compile(
        r'\|\s*\*\*?(\d{2,3})%?\*\*?\s*\|\s*(.+?)\s*\|\s*\*\*?(.+?)\*\*?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*\[(?:Apply / View|Apply)\]\((https?://[^\)]+)\)\s*\|'
    )
    
    # Fallback 7-column pattern
    row_pattern_7col = re.compile(
        r'\|\s*\*\*?(\d{2,3})%?\*\*?\s*\|\s*(.+?)\s*\|\s*\*\*?(.+?)\*\*?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*\[(?:Apply / View|Apply)\]\((https?://[^\)]+)\)\s*\|'
    )
    
    raw_scouted = []
    
    matches_8 = list(row_pattern_8col.finditer(content))
    if matches_8:
        for match in matches_8:
            score = int(match.group(1))
            title = match.group(2).strip()
            company = match.group(3).strip()
            location = match.group(4).strip()
            lang_col = match.group(5).strip()
            platform = match.group(6).strip()
            status_tag = match.group(7).strip()
            url = match.group(8).strip()
            
            raw_scouted.append({
                "score": score,
                "title": title,
                "company": company,
                "location": location,
                "lang_col": lang_col,
                "platform": platform,
                "status_tag": status_tag,
                "url": url
            })
    else:
        for match in row_pattern_7col.finditer(content):
            score = int(match.group(1))
            title = match.group(2).strip()
            company = match.group(3).strip()
            location = match.group(4).strip()
            platform = match.group(5).strip()
            status_tag = match.group(6).strip()
            url = match.group(7).strip()
            
            raw_scouted.append({
                "score": score,
                "title": title,
                "company": company,
                "location": location,
                "lang_col": "",
                "platform": platform,
                "status_tag": status_tag,
                "url": url
            })
            
    # Cross-site deduplication and prepared exclusion
    seen_keys = set()
    
    for item in raw_scouted:
        title = item["title"]
        company = item["company"]
        location = item["location"]
        platform = item["platform"]
        url = item["url"]
        score = item["score"]
        lang_col = item["lang_col"]
        
        c_norm = normalize_company(company)
        t_norm = normalize_title(title)
        composite_key = f"{c_norm}_{t_norm}"
        
        # Check if already in prepared applications
        if composite_key in prepared_keys:
            continue
        if c_norm and len(c_norm) > 3 and c_norm in prepared_keys:
            # Check if this company already has a prepared application
            # If the title is similar, skip duplicate
            continue
            
        # Check if duplicate within scouted
        if composite_key in seen_keys:
            continue
        seen_keys.add(composite_key)
        
        # Determine language details
        if "Finnish Required" in lang_col or "🇫🇮" in lang_col:
            lang_tag = "Finnish Required"
            lang_badge = "🇫🇮 Finnish Required"
            lang_class = "border-amber-500/30 bg-amber-500/10 text-amber-300"
        elif "Finnish Advantage" in lang_col or "👍" in lang_col:
            lang_tag = "Finnish Advantage"
            lang_badge = "👍 Finnish Advantage"
            lang_class = "border-sky-500/30 bg-sky-500/10 text-sky-300"
        else:
            lang_info = detect_language_requirement(title, "")
            lang_tag = lang_info["tag"]
            lang_badge = lang_info["badge"]
            lang_class = lang_info["class"]

        scout_id = f"scout_{c_norm}_{t_norm[:10]}_{score}"
        db_record = saved_db.get(scout_id, {})
        status = db_record.get("status", "scouted")
        
        scouted_jobs.append({
            "id": scout_id,
            "folder": None,
            "type": "scouted",
            "title": title,
            "company": company,
            "location": location,
            "platform": platform,
            "platform_display": platform,
            "platforms": [p.strip() for p in platform.split("+")],
            "url": url,
            "match_score": score,
            "ats_score": None,
            "status": status,
            "language_tag": lang_tag,
            "language_badge": lang_badge,
            "language_class": lang_class,
            "applied_date": db_record.get("applied_date", ""),
            "portal": platform,
            "notes": db_record.get("notes", ""),
            "interview_date": db_record.get("interview_date", "")
        })
        
    return scouted_jobs

# --- Authentication & Security Routes ---

@app.route("/healthz")
def healthz():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()}), 200

@app.route("/login", methods=["GET", "POST"])
def login():
    global AUTH_CONFIG
    AUTH_CONFIG = load_auth_config()
    client_ip = get_client_ip()

    if is_client_authenticated():
        return redirect("/")

    is_allowed, wait_seconds = check_rate_limit(client_ip)
    if not is_allowed:
        wait_mins = (wait_seconds // 60) + 1
        return render_template(
            "login.html",
            error=f"Too many failed attempts. Security lockout active. Please wait {wait_mins} minute(s).",
            stage="credentials",
            csrf_token=get_or_create_csrf_token()
        ), 429

    if request.method == "GET":
        # Check if user is in MFA verification stage
        if session.get("pending_user") and session.get("pending_authenticated"):
            if not AUTH_CONFIG.get("totp_secret"):
                if "setup_totp_secret" not in session:
                    session["setup_totp_secret"] = generate_totp_secret()
                    session["setup_recovery_codes"] = generate_recovery_codes(8)
                totp_uri = get_totp_uri(session["pending_user"], session["setup_totp_secret"])
                qr_svg = generate_qr_svg(totp_uri)
                return render_template(
                    "login.html",
                    stage="setup_mfa",
                    totp_secret=session["setup_totp_secret"],
                    qr_svg=qr_svg,
                    recovery_codes=session["setup_recovery_codes"],
                    csrf_token=get_or_create_csrf_token(),
                    next_url=request.args.get("next", "/")
                )
            return render_template(
                "login.html",
                stage="mfa",
                csrf_token=get_or_create_csrf_token(),
                next_url=request.args.get("next", "/")
            )
        
        msg = "You have been logged out securely." if request.args.get("msg") == "logged_out" else None
        return render_template(
            "login.html",
            stage="credentials",
            message=msg,
            csrf_token=get_or_create_csrf_token(),
            next_url=request.args.get("next", "/")
        )

    # POST credentials
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    next_url = request.form.get("next") or "/"

    if not verify_credentials(username, password, AUTH_CONFIG):
        remaining = record_failed_attempt(client_ip)
        if remaining == 0:
            return render_template(
                "login.html",
                error="Too many failed attempts. Security lockout active for 15 minutes.",
                stage="credentials",
                csrf_token=get_or_create_csrf_token()
            ), 429
        return render_template(
            "login.html",
            error=f"Invalid username or password. {remaining} attempt(s) remaining.",
            stage="credentials",
            csrf_token=get_or_create_csrf_token()
        ), 401

    reset_rate_limit(client_ip)

    if AUTH_CONFIG.get("mfa_enabled"):
        session["pending_user"] = username
        session["pending_authenticated"] = True
        session["next_url"] = next_url
        return redirect("/login")

    session.permanent = True
    session["authenticated"] = True
    session["user"] = username
    return redirect(next_url)

@app.route("/login/verify-mfa", methods=["POST"])
def verify_mfa_post():
    global AUTH_CONFIG
    AUTH_CONFIG = load_auth_config()
    client_ip = get_client_ip()

    if not session.get("pending_user") or not session.get("pending_authenticated"):
        return redirect("/login")

    is_allowed, wait_seconds = check_rate_limit(client_ip)
    if not is_allowed:
        wait_mins = (wait_seconds // 60) + 1
        return render_template(
            "login.html",
            error=f"Security lockout active. Please wait {wait_mins} minute(s).",
            stage="mfa",
            csrf_token=get_or_create_csrf_token()
        ), 429

    totp_token = request.form.get("totp_token", "").strip()
    recovery_code = request.form.get("recovery_code", "").strip()
    next_url = session.get("next_url") or "/"

    if recovery_code:
        if consume_recovery_code(recovery_code, AUTH_CONFIG):
            reset_rate_limit(client_ip)
            session.permanent = True
            session["authenticated"] = True
            session["mfa_verified"] = True
            session["user"] = session.pop("pending_user")
            session.pop("pending_authenticated", None)
            return redirect(next_url)
        else:
            remaining = record_failed_attempt(client_ip)
            return render_template(
                "login.html",
                error=f"Invalid recovery code. {remaining} attempt(s) remaining.",
                stage="mfa",
                csrf_token=get_or_create_csrf_token()
            ), 401

    secret = AUTH_CONFIG.get("totp_secret", "")
    if verify_totp(secret, totp_token):
        reset_rate_limit(client_ip)
        session.permanent = True
        session["authenticated"] = True
        session["mfa_verified"] = True
        session["user"] = session.pop("pending_user")
        session.pop("pending_authenticated", None)
        return redirect(next_url)

    remaining = record_failed_attempt(client_ip)
    return render_template(
        "login.html",
        error=f"Invalid verification code. {remaining} attempt(s) remaining.",
        stage="mfa",
        csrf_token=get_or_create_csrf_token()
    ), 401

@app.route("/login/setup-mfa", methods=["POST"])
def setup_mfa_post():
    global AUTH_CONFIG
    AUTH_CONFIG = load_auth_config()

    if not session.get("pending_user") or not session.get("pending_authenticated"):
        return redirect("/login")

    setup_secret = session.get("setup_totp_secret")
    recovery_codes = session.get("setup_recovery_codes", [])
    totp_token = request.form.get("totp_token", "").strip()

    if not setup_secret or not verify_totp(setup_secret, totp_token):
        totp_uri = get_totp_uri(session["pending_user"], setup_secret or "")
        qr_svg = generate_qr_svg(totp_uri)
        return render_template(
            "login.html",
            error="Invalid code. Please verify the 6-digit code shown in your authenticator app.",
            stage="setup_mfa",
            totp_secret=setup_secret,
            qr_svg=qr_svg,
            recovery_codes=recovery_codes,
            csrf_token=get_or_create_csrf_token()
        ), 400

    AUTH_CONFIG["mfa_enabled"] = True
    AUTH_CONFIG["totp_secret"] = setup_secret
    AUTH_CONFIG["recovery_codes"] = recovery_codes
    save_auth_config(AUTH_CONFIG)

    session.pop("setup_totp_secret", None)
    session.pop("setup_recovery_codes", None)
    session.permanent = True
    session["authenticated"] = True
    session["mfa_verified"] = True
    session["user"] = session.pop("pending_user")
    session.pop("pending_authenticated", None)
    next_url = session.pop("next_url", "/")
    return redirect(next_url)

@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect("/login?msg=logged_out")

# --- Core Web Dashboard & API Routes ---

@app.route("/")
def index():
    csrf_token = get_or_create_csrf_token() if AUTH_CONFIG.get("auth_enabled") else ""
    user = session.get("user") if AUTH_CONFIG.get("auth_enabled") else None
    return render_template("index.html", csrf_token=csrf_token, user=user)

@app.route("/api/jobs")
def get_jobs():
    prepared = scan_prepared_applications()
    scouted = scan_scouted_feed(prepared)
    
    all_jobs = prepared + scouted
    
    # Sort by match score descending
    all_jobs.sort(key=lambda x: (x.get("match_score") or 0), reverse=True)
    
    # Calculate counts
    counts = {
        "all": len(all_jobs),
        "scouted": sum(1 for j in all_jobs if j["status"] == "scouted"),
        "ready": sum(1 for j in all_jobs if j["status"] == "ready"),
        "applied": sum(1 for j in all_jobs if j["status"] == "applied"),
        "interviewing": sum(1 for j in all_jobs if j["status"] == "interviewing"),
        "offer": sum(1 for j in all_jobs if j["status"] == "offer"),
        "rejected": sum(1 for j in all_jobs if j["status"] == "rejected"),
        "english": sum(1 for j in all_jobs if j.get("language_tag") == "English / International"),
        "finnish_req": sum(1 for j in all_jobs if j.get("language_tag") == "Finnish Required"),
        "finnish_adv": sum(1 for j in all_jobs if j.get("language_tag") == "Finnish Advantage")
    }
    
    return jsonify({
        "counts": counts,
        "jobs": all_jobs
    })

@app.route("/api/jobs/update_status", methods=["POST"])
def update_status():
    payload = request.json or {}
    job_id = payload.get("id")
    new_status = payload.get("status")
    portal = payload.get("portal")
    
    if not job_id or not new_status:
        return jsonify({"error": "Missing id or status"}), 400
        
    db = load_pipeline_data()
    if job_id not in db:
        db[job_id] = {}
        
    db[job_id]["status"] = new_status
    
    if new_status == "applied" and not db[job_id].get("applied_date"):
        db[job_id]["applied_date"] = datetime.now().strftime("%Y-%m-%d")
        
    if portal:
        db[job_id]["portal"] = portal
    url = payload.get("url")
    if url and not is_candidate_personal_url(url):
        db[job_id]["url"] = url
        
    save_pipeline_data(db)

    # When status shifts to 'interviewing', automatically generate interview prep if not already present
    prep_generated = False
    prep_filename = None
    if new_status == "interviewing":
        folder = payload.get("folder") or job_id
        folder_path = WORKSPACE_DIR / folder
        if folder_path.exists() and folder_path.is_dir():
            role_info = extract_role_info_from_jd(folder_path, folder)
            try:
                prep_path = generate_interview_prep(
                    folder_path=folder_path,
                    title=role_info.get("title", folder.replace("_", " ")),
                    company=role_info.get("company", "Company"),
                    location=role_info.get("location", "Finland")
                )
                prep_generated = True
                prep_filename = prep_path.name
            except Exception as e:
                print(f"Error generating interview prep on status change: {e}")

    # Telegram alert for status transitions
    try:
        title_for_alert = payload.get("title") or job_id.replace("_", " ")
        company_for_alert = payload.get("company") or "Company"
        notify_status_update(company_for_alert, title_for_alert, new_status)
    except Exception as e:
        print(f"Notice: Telegram notification failed: {e}")

    return jsonify({
        "success": True,
        "record": db[job_id],
        "prep_generated": prep_generated,
        "prep_filename": prep_filename
    })

@app.route("/api/jobs/generate_prep", methods=["POST"])
def api_generate_prep():
    """Explicit endpoint to generate interview prep on demand."""
    payload = request.json or {}
    folder = payload.get("folder") or payload.get("id")
    if not folder:
        return jsonify({"error": "Missing folder or id"}), 400
        
    folder_path = WORKSPACE_DIR / folder
    if not folder_path.exists() or not folder_path.is_dir():
        return jsonify({"error": f"Folder '{folder}' not found"}), 404
        
    role_info = extract_role_info_from_jd(folder_path, folder)
    force = payload.get("force", False)
    
    try:
        prep_path = generate_interview_prep(
            folder_path=folder_path,
            title=role_info.get("title", folder.replace("_", " ")),
            company=role_info.get("company", "Company"),
            location=role_info.get("location", "Finland"),
            force=force
        )
        content = prep_path.read_text(encoding="utf-8", errors="ignore")
        return jsonify({
            "success": True,
            "filename": prep_path.name,
            "content": content
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/jobs/notes", methods=["POST"])
def update_notes():
    payload = request.json or {}
    job_id = payload.get("id")
    notes = payload.get("notes", "")
    interview_date = payload.get("interview_date", "")
    url = payload.get("url")
    portal = payload.get("portal")
    
    if not job_id:
        return jsonify({"error": "Missing id"}), 400
        
    db = load_pipeline_data()
    if job_id not in db:
        db[job_id] = {}
        
    db[job_id]["notes"] = notes
    if interview_date:
        db[job_id]["interview_date"] = interview_date
    if portal:
        db[job_id]["portal"] = portal
    if url and not is_candidate_personal_url(url):
        db[job_id]["url"] = url
        
    save_pipeline_data(db)
    return jsonify({"success": True, "record": db[job_id]})

@app.route("/api/files/<folder>/<filename>")
def get_file(folder, filename):
    safe_folder = Path(folder).name
    safe_filename = Path(filename).name
    folder_path = (WORKSPACE_DIR / safe_folder).resolve()
    file_path = (folder_path / safe_filename).resolve()
    
    if not file_path.is_relative_to(WORKSPACE_DIR) or not file_path.exists() or not file_path.is_file():
        abort(404)
        
    if safe_filename.endswith(".pdf"):
        return send_file(file_path, mimetype="application/pdf")
    else:
        return send_file(file_path, mimetype="text/markdown")

@app.route("/api/content/<folder>/<doc_type>")
def get_content(folder, doc_type):
    """Retrieve markdown text content for the Copilot drawer."""
    folder_path = WORKSPACE_DIR / folder
    if not folder_path.exists():
        return jsonify({"error": "Folder not found"}), 404
        
    pattern_map = {
        "cv": "*CV*.md",
        "cover_letter": "*Cover_Letter*.md",
        "screening_qa": "*Application_Form_Answers*.md",
        "qa": "*Application_Form_Answers*.md",
        "outreach": "*Interview_Prep*.md",
        "prep": "*Interview_Prep*.md",
        "ats": "*ATS*.md",
        "jd": "*Job_Description*.md"
    }
    
    pattern = pattern_map.get(doc_type, "*.md")
    matching = list(folder_path.glob(pattern))
    
    if not matching:
        return jsonify({
            "not_found": True,
            "doc_type": doc_type,
            "content": f"No {doc_type} document found for this application."
        })
        
    try:
        content = matching[0].read_text(encoding="utf-8", errors="ignore")
        return jsonify({
            "filename": matching[0].name,
            "content": content
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/open_folder", methods=["POST"])
def open_folder():
    """Open application folder in native system file manager (xdg-open) when desktop is present."""
    payload = request.json or {}
    folder = payload.get("folder")
    if not folder:
        return jsonify({"error": "Missing folder parameter"}), 400
        
    safe_folder = Path(folder).name
    folder_path = (WORKSPACE_DIR / safe_folder).resolve()
    if not folder_path.is_relative_to(WORKSPACE_DIR) or not folder_path.exists() or not folder_path.is_dir():
        return jsonify({"error": f"Folder '{folder}' does not exist on disk"}), 404
        
    abs_path = str(folder_path)
    opened = False
    error_msg = None
    
    # Check if a display server is running (desktop vs headless server)
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if not has_display:
        return jsonify({
            "success": True,
            "opened": False,
            "path": safe_folder,
            "folder": safe_folder,
            "message": "Headless server environment: folder exists, GUI file manager unavailable."
        })

    try:
        subprocess.Popen(["xdg-open", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        opened = True
    except Exception as e:
        error_msg = str(e)
        
    return jsonify({
        "success": True,
        "opened": opened,
        "path": abs_path,
        "folder": safe_folder,
        "error": error_msg
    })

@app.route("/api/workflow/generate", methods=["POST"])
def trigger_workflow_generate():
    """Trigger 1-click Application Package Workflow for a scouted or specified role."""
    payload = request.json or {}
    title = payload.get("title")
    company = payload.get("company")
    url = payload.get("url", "")
    location = payload.get("location", "Finland")
    description = payload.get("description", "")
    folder = payload.get("folder")
    
    if not url and folder:
        url = KNOWN_JOB_URLS.get(folder, "")
        
    if not title or not company:
        return jsonify({"error": "Missing title or company"}), 400
        
    try:
        result = generate_application_package(
            title=title,
            company=company,
            url=url,
            location=location,
            description=description,
            folder_override=folder
        )
        # Register in KNOWN_JOB_URLS if url provided
        if result.get("folder") and url and not is_candidate_personal_url(url):
            KNOWN_JOB_URLS[result["folder"]] = url

        # Send Telegram notification for created package
        try:
            notify_package_generated(
                company=company,
                title=title,
                folder_name=result.get("folder", ""),
                has_ai=bool(os.environ.get("GEMINI_API_KEY") or (WORKSPACE_DIR / ".env").exists())
            )
        except Exception as e:
            print(f"Notice: Telegram notification failed: {e}")
            
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/telegram/status")
def telegram_status():
    """Check if Telegram is configured and get notification threshold."""
    config = get_telegram_config()
    return jsonify({
        "enabled": config["enabled"],
        "min_score": config["min_score"],
        "has_token": bool(config["bot_token"]),
        "has_chat_id": bool(config["chat_id"])
    })

@app.route("/api/telegram/test", methods=["POST"])
def telegram_test():
    """Send a test message to Telegram."""
    res = test_telegram_connection()
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code

@app.route("/api/retention/status")
def retention_status():
    """Return current retention policy configuration and cleanup candidate list."""
    days_param = request.args.get("days", type=int)
    cfg = get_retention_config()
    scan = scan_retention_candidates(days=days_param)
    return jsonify({
        "config": {
            "days": cfg["days"],
            "protect_statuses": list(cfg["protect_statuses"]),
            "mode": cfg["mode"],
            "enabled": cfg["enabled"]
        },
        "scan": scan
    })

@app.route("/api/retention/cleanup", methods=["POST"])
def retention_cleanup():
    """Trigger retention cleanup (supports dry_run or execute)."""
    payload = request.json or {}
    dry_run = payload.get("dry_run", False)
    days = payload.get("days")
    mode = payload.get("mode", "full")
    protect_list = payload.get("protect")
    protect_set = set(protect_list) if protect_list else None

    result = clean_expired_postings(
        days=days,
        protect_statuses=protect_set,
        mode=mode,
        dry_run=dry_run
    )
    return jsonify(result)

@app.route("/api/jobs/toggle_pin", methods=["POST"])
def toggle_pin():
    """Toggle persistent pinned protection for an application package."""
    payload = request.json or {}
    job_id = payload.get("id")
    if not job_id:
        return jsonify({"error": "Missing job id"}), 400

    db = load_pipeline_data()
    if job_id not in db:
        db[job_id] = {}

    current_pin = db[job_id].get("pinned", False)
    new_pin = payload.get("pinned", not current_pin)
    db[job_id]["pinned"] = new_pin
    save_pipeline_data(db)

    return jsonify({
        "success": True,
        "id": job_id,
        "pinned": new_pin
    })

def get_env_file_path() -> Path:
    env_path = WORKSPACE_DIR / ".env"
    if not env_path.exists():
        alt_path = Path(__file__).resolve().parent.parent / ".env"
        if alt_path.exists():
            return alt_path
    return env_path

def read_raw_env_dict() -> dict:
    env_path = get_env_file_path()
    res = {}
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    res[k] = v
        except Exception:
            pass
    for k, v in os.environ.items():
        if k not in res and v:
            res[k] = v
    return res

def write_env_dict(updates: dict):
    env_path = get_env_file_path()
    lines = []
    seen = set()
    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []
    
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            k = k.strip()
            if k in updates:
                seen.add(k)
                val = updates[k]
                if isinstance(val, bool):
                    val_str = "true" if val else "false"
                else:
                    val_str = str(val).strip()
                if any(c in val_str for c in [" ", "#", "=", "\t", ","]) or not val_str.isalnum():
                    val_str = f'"{val_str}"'
                new_lines.append(f"{k}={val_str}")
                continue
        new_lines.append(line)
        
    for k, val in updates.items():
        if k not in seen:
            if isinstance(val, bool):
                val_str = "true" if val else "false"
            else:
                val_str = str(val).strip()
            if any(c in val_str for c in [" ", "#", "=", "\t", ","]) or not val_str.isalnum():
                val_str = f'"{val_str}"'
            new_lines.append(f"{k}={val_str}")
            
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    
    # Update current process environment
    for k, val in updates.items():
        os.environ[k] = str(val) if not isinstance(val, bool) else ("true" if val else "false")

def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "****"
    return secret[:4] + "..." + secret[-4:]

@app.route("/api/settings", methods=["GET"])
def get_settings_api():
    """Return current tool configuration with sensitive credentials masked."""
    env = read_raw_env_dict()
    gemini_key = env.get("GEMINI_API_KEY", "")
    telegram_token = env.get("TELEGRAM_BOT_TOKEN", "")
    
    default_queries = "Junior IT, Junior Security, Junior Systems Administrator, IT Support Specialist, Service Desk Analyst, Data Center Technician, Field Service Technician, SOC Analyst, IT Specialist, Cybersecurity, IT Trainee, System Administrator, Sec Ops, Dev ops, Junior Sec Ops, DV Ops"
    
    data = {
        "gemini": {
            "has_key": bool(gemini_key),
            "masked_key": mask_secret(gemini_key),
        },
        "telegram": {
            "enabled": bool(telegram_token and env.get("TELEGRAM_CHAT_ID")),
            "has_token": bool(telegram_token),
            "masked_token": mask_secret(telegram_token),
            "chat_id": env.get("TELEGRAM_CHAT_ID", ""),
            "min_score": int(env.get("TELEGRAM_MIN_MATCH_SCORE", 80)),
        },
        "scout": {
            "enabled": env.get("SCOUT_ENABLED", "false").lower() in ("true", "1", "yes"),
            "interval_hours": int(env.get("SCOUT_INTERVAL_HOURS", 2)),
            "location": env.get("SCOUT_LOCATION", "Finland"),
            "lookback_hours": int(env.get("SCOUT_LOOKBACK_HOURS", 168)),
            "limit": int(env.get("SCOUT_LIMIT_PER_QUERY", 10)),
            "queries": env.get("SCOUT_QUERIES", default_queries),
            "platforms": env.get("SCOUT_PLATFORMS", "linkedin,indeed,google,duunitori,arbeitnow"),
        },
        "retention": {
            "enabled": int(env.get("POSTING_RETENTION_DAYS", 30)) > 0,
            "days": int(env.get("POSTING_RETENTION_DAYS", 30)),
            "mode": env.get("RETENTION_MODE", "full"),
            "protect_statuses": env.get("RETENTION_PROTECT_STATUS", "interviewing,offer,applied"),
            "notify_telegram": env.get("RETENTION_NOTIFY_TELEGRAM", "true").lower() in ("true", "1", "yes"),
        },
        "security": {
            "auth_enabled": AUTH_CONFIG.get("auth_enabled", False),
            "mfa_enabled": AUTH_CONFIG.get("mfa_enabled", False),
            "admin_username": AUTH_CONFIG.get("username", "admin"),
        }
    }
    return jsonify(data)

@app.route("/api/settings", methods=["POST"])
def update_settings_api():
    """Save tool configuration to .env and hot-reload process environment."""
    payload = request.json or {}
    current_env = read_raw_env_dict()
    updates = {}
    
    # 1. Gemini
    if "gemini_api_key" in payload:
        new_key = payload["gemini_api_key"].strip()
        if new_key and "..." not in new_key and "****" not in new_key:
            updates["GEMINI_API_KEY"] = new_key
            
    # 2. Telegram
    if "telegram_bot_token" in payload:
        new_tok = payload["telegram_bot_token"].strip()
        if new_tok and "..." not in new_tok and "****" not in new_tok:
            updates["TELEGRAM_BOT_TOKEN"] = new_tok
    if "telegram_chat_id" in payload:
        updates["TELEGRAM_CHAT_ID"] = str(payload["telegram_chat_id"]).strip()
    if "telegram_min_match_score" in payload:
        try:
            updates["TELEGRAM_MIN_MATCH_SCORE"] = str(int(payload["telegram_min_match_score"]))
        except (ValueError, TypeError):
            pass
            
    # 3. Scout
    if "scout_enabled" in payload:
        updates["SCOUT_ENABLED"] = "true" if payload["scout_enabled"] else "false"
    if "scout_interval_hours" in payload:
        try:
            val = max(1, min(72, int(payload["scout_interval_hours"])))
            updates["SCOUT_INTERVAL_HOURS"] = str(val)
        except (ValueError, TypeError):
            pass
    if "scout_location" in payload:
        updates["SCOUT_LOCATION"] = payload["scout_location"].strip() or "Finland"
    if "scout_lookback_hours" in payload:
        try:
            updates["SCOUT_LOOKBACK_HOURS"] = str(int(payload["scout_lookback_hours"]))
        except (ValueError, TypeError):
            pass
    if "scout_limit_per_query" in payload:
        try:
            updates["SCOUT_LIMIT_PER_QUERY"] = str(int(payload["scout_limit_per_query"]))
        except (ValueError, TypeError):
            pass
    if "scout_queries" in payload:
        updates["SCOUT_QUERIES"] = payload["scout_queries"].strip()
    if "scout_platforms" in payload:
        if isinstance(payload["scout_platforms"], list):
            updates["SCOUT_PLATFORMS"] = ",".join(payload["scout_platforms"])
        else:
            updates["SCOUT_PLATFORMS"] = str(payload["scout_platforms"]).strip()
            
    # 4. Retention
    if "retention_days" in payload:
        try:
            updates["POSTING_RETENTION_DAYS"] = str(int(payload["retention_days"]))
        except (ValueError, TypeError):
            pass
    if "retention_mode" in payload:
        if payload["retention_mode"] in ("full", "pdfs_only"):
            updates["RETENTION_MODE"] = payload["retention_mode"]
    if "retention_protect_status" in payload:
        updates["RETENTION_PROTECT_STATUS"] = str(payload["retention_protect_status"]).strip()
    if "retention_notify_telegram" in payload:
        updates["RETENTION_NOTIFY_TELEGRAM"] = "true" if payload["retention_notify_telegram"] else "false"
        
    if updates:
        write_env_dict(updates)
        
        # If scout interval was updated, synchronize crontab on Linux if running as opc/host
        if "SCOUT_INTERVAL_HOURS" in updates:
            try:
                import subprocess
                hours = updates["SCOUT_INTERVAL_HOURS"]
                cmd = f"(crontab -l 2>/dev/null | grep -v 'cron_scout.sh'; echo '# Job Hunt Command Center - Autonomous IT Scout Scan (Every {hours} hours)'; echo '0 */{hours} * * * /home/opc/jobhunt/scripts/cron_scout.sh >/dev/null 2>&1') | crontab -"
                subprocess.run(cmd, shell=True, timeout=3, capture_output=True)
            except Exception:
                pass
        
    return jsonify({
        "success": True,
        "message": "Settings updated successfully!",
        "updated_keys": list(updates.keys())
    })

@app.route("/api/folder_files/<folder>")
def get_folder_files(folder):
    """Return a detailed list of all files inside an application folder."""
    folder_path = WORKSPACE_DIR / folder
    if not folder_path.exists() or not folder_path.is_dir():
        return jsonify({"error": "Folder not found"}), 404
        
    files = []
    for f in folder_path.iterdir():
        if f.is_file():
            stat = f.stat()
            if "Cover_Letter" in f.name:
                category = "cover_letter"
            elif "Job_Description" in f.name:
                category = "jd"
            elif "Analysis" in f.name:
                category = "analysis"
            elif "ATS" in f.name:
                category = "ats"
            elif "Application_Form_Answers" in f.name:
                category = "qa"
            elif "Interview_Prep" in f.name:
                category = "prep"
            elif "Outreach" in f.name:
                category = "outreach"
            else:
                category = "cv"
                
            files.append({
                "name": f.name,
                "category": category,
                "is_pdf": f.name.endswith(".pdf"),
                "is_md": f.name.endswith(".md"),
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            })
            
    category_order = {"cv": 1, "cover_letter": 2, "jd": 3, "analysis": 4, "qa": 5, "prep": 6, "ats": 7, "other": 8}
    files.sort(key=lambda x: (not x["is_pdf"], category_order.get(x["category"], 9), x["name"]))
    
    return jsonify({
        "folder": folder,
        "path": str(folder_path.resolve()),
        "count": len(files),
        "files": files
    })

@app.route("/api/bundle/<folder>")
def download_application_bundle(folder):
    """Package application PDFs and Q&A documents into a single downloadable zip file."""
    folder_path = WORKSPACE_DIR / folder
    if not folder_path.exists() or not folder_path.is_dir():
        return jsonify({"error": "Folder not found"}), 404
        
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in folder_path.iterdir():
            if f.is_file() and (f.suffix in [".pdf", ".md"] and not f.name.startswith(".")):
                zf.write(f, arcname=f.name)
                
    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{folder}_Application_Package.zip"
    )

@app.route("/api/outreach/<folder>")
def generate_outreach_drafts(folder):
    """Generate tailored LinkedIn connection request, InMail, and follow-up templates."""
    folder_path = WORKSPACE_DIR / folder
    if not folder_path.exists() or not folder_path.is_dir():
        return jsonify({"error": "Folder not found"}), 404
        
    role_info = extract_role_info_from_jd(folder_path, folder)
    title = role_info["title"]
    company = role_info["company"]
    
    # 1. LinkedIn Connection Request Note (< 400 chars)
    linkedin_connect = (
        f"Hi! I'm an IT systems & infrastructure engineer based in Finland (TUAS B.Eng., 4.0 GPA). "
        f"I saw the {title} opening at {company} and wanted to reach out. "
        f"With 8+ yrs in enterprise systems, 94% first-time-fix rate, 0-day notice, and permanent EU authorization, I'd love to connect and follow your team's work!"
    )
    if len(linkedin_connect) > 395:
        linkedin_connect = (
            f"Hi! I'm an IT systems engineer in Finland (TUAS 4.0 GPA, 8+ yrs infra). "
            f"I saw the {title} role at {company} and would love to connect. "
            f"With 0-day notice and EU authorization, I'm eager to discuss how I can support your team."
        )

    # 2. Hiring Manager / Recruiter InMail / Direct Email
    cand = get_candidate_contact_info(WORKSPACE_DIR)
    hiring_inmail = (
        f"Dear {company} Hiring Team,\n\n"
        f"I recently applied for the {title} position and wanted to reach out directly. "
        f"With 8+ years of enterprise systems administration, security operations (Defender XDR, Sentinel, Wazuh SIEM), "
        f"and practical TryHackMe certifications (SOC Level 1, PenTest+), my background aligns directly with the hands-on "
        f"depth your team needs.\n\n"
        f"I'm based in Finland with full EU work authorization and 0 days notice period. "
        f"I would welcome the opportunity to discuss how my automation and prevention-first approach can support {company}.\n\n"
        f"Best regards,\n{cand['name']}\n{cand['phone']} | {cand['email']}"
    )
    
    # 3. 7-Day Follow-Up Message
    follow_up = (
        f"Hi {company} Hiring Team,\n\n"
        f"I hope your week is going well! I am following up on my application for the {title} position submitted recently. "
        f"I remain very enthusiastic about the opportunity to contribute to {company} with my background in enterprise systems, "
        f"cloud security, and automated incident response.\n\n"
        f"Please let me know if you need any additional portfolio samples, references, or details from my side.\n\n"
        f"Best regards,\n{cand['name']}\n{cand['email']} | {cand['phone']}"
    )

    # Check for AI-tailored drafts file (cold-email-writer & linkedin-profile-optimizer)
    drafts_file = folder_path / f"Outreach_Drafts_{folder}.json"
    if drafts_file.exists():
        try:
            saved_drafts = json.loads(drafts_file.read_text(encoding="utf-8"))
            if saved_drafts.get("linkedin_connect"):
                linkedin_connect = saved_drafts["linkedin_connect"]
            if saved_drafts.get("recruiter_inmail"):
                hiring_inmail = saved_drafts["recruiter_inmail"]
            if saved_drafts.get("follow_up"):
                follow_up = saved_drafts["follow_up"]
        except Exception:
            pass
    
    # Find PDF files for easy copying
    cv_pdfs = [str(f.resolve()) for f in folder_path.glob("*.pdf") if "Cover_Letter" not in f.name]
    cl_pdfs = [str(f.resolve()) for f in folder_path.glob("*Cover_Letter*.pdf")]
    
    return jsonify({
        "folder": folder,
        "title": title,
        "company": company,
        "linkedin_connect": linkedin_connect,
        "hiring_inmail": hiring_inmail,
        "follow_up": follow_up,
        "cv_pdf_path": cv_pdfs[0] if cv_pdfs else None,
        "cl_pdf_path": cl_pdfs[0] if cl_pdfs else None
    })

@app.route("/api/top-choice/<folder>")
def get_top_choice_pitch_for_folder(folder):
    """Generate or retrieve cached 'Why Top Choice' LinkedIn pitch for an application folder."""
    folder_path = WORKSPACE_DIR / folder
    if not folder_path.exists() or not folder_path.is_dir():
        return jsonify({"error": "Folder not found"}), 404
        
    role_info = extract_role_info_from_jd(folder_path, folder)
    title = role_info["title"]
    company = role_info["company"]
    location = role_info.get("location", "Finland")

    jd_text = ""
    for f in list(folder_path.glob("*Job_Description*.md")) + list(folder_path.glob("*Analysis*.md")):
        try:
            jd_text = f.read_text(encoding="utf-8", errors="ignore")
            if len(jd_text) > 100:
                break
        except Exception:
            pass

    cache_file = folder_path / f"Top_Choice_Pitch_{folder}.json"
    regenerate = request.args.get("regenerate", "false").lower() in ("true", "1")
    if cache_file.exists() and not regenerate:
        try:
            cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached_data.get("why_top_choice_candidate"):
                cached_data["cached"] = True
                return jsonify(cached_data)
        except Exception:
            pass

    try:
        from scripts.ai_tailor import generate_top_choice_pitch
    except ImportError:
        try:
            from ai_tailor import generate_top_choice_pitch
        except ImportError:
            generate_top_choice_pitch = None

    if generate_top_choice_pitch:
        pitch_data = generate_top_choice_pitch(title=title, company=company, location=location, jd_text=jd_text)
    else:
        pitch_data = {
            "title": title,
            "company": company,
            "location": location,
            "why_top_choice_candidate": f"Top choice for {company}'s {title}: 4.0 GPA ICT (TUAS) + 8+ yrs enterprise infra. Achieved 94% First-Time-Fix rate across 200+ endpoints with automation. Turnkey hire in Finland with permanent EU authorization, 0-day notice, and Supo readiness.",
            "why_top_choice_company": f"Motivation statement for {company}",
            "linkedin_quick_pitch": f"Hi! I'm an IT systems engineer based in Finland (TUAS 4.0 GPA, 8+ yrs infra). I saw the {title} role at {company} and would love to connect. With 0-day notice and permanent EU authorization, I'm eager to discuss how I can bring immediate value to your team!",
            "linkedin_post_draft": f"Excited about the {title} opportunity at {company}!",
            "matched_skills": []
        }

    try:
        cache_file.write_text(json.dumps(pitch_data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    pitch_data["cached"] = False
    return jsonify(pitch_data)

@app.route("/api/top-choice", methods=["POST"])
def generate_top_choice_custom():
    """Generate on-the-fly 'Why Top Choice' pitch for any job (folder, scout feed, or manual)."""
    payload = request.get_json(silent=True) or {}
    folder = payload.get("folder", "").strip()
    
    if folder:
        folder_path = WORKSPACE_DIR / folder
        if folder_path.exists() and folder_path.is_dir():
            role_info = extract_role_info_from_jd(folder_path, folder)
            title = payload.get("title") or role_info["title"]
            company = payload.get("company") or role_info["company"]
            location = payload.get("location") or role_info.get("location", "Finland")
            
            jd_text = payload.get("description", "")
            if not jd_text:
                for f in list(folder_path.glob("*Job_Description*.md")) + list(folder_path.glob("*Analysis*.md")):
                    try:
                        jd_text = f.read_text(encoding="utf-8", errors="ignore")
                        if len(jd_text) > 100:
                            break
                    except Exception:
                        pass
        else:
            title = payload.get("title", "IT Specialist")
            company = payload.get("company", "Company")
            location = payload.get("location", "Finland")
            jd_text = payload.get("description", "")
    else:
        title = payload.get("title", "IT Specialist")
        company = payload.get("company", "Company")
        location = payload.get("location", "Finland")
        jd_text = payload.get("description", "")

    try:
        from scripts.ai_tailor import generate_top_choice_pitch
    except ImportError:
        try:
            from ai_tailor import generate_top_choice_pitch
        except ImportError:
            generate_top_choice_pitch = None

    if generate_top_choice_pitch:
        pitch_data = generate_top_choice_pitch(title=title, company=company, location=location, jd_text=jd_text)
    else:
        pitch_data = {
            "title": title,
            "company": company,
            "location": location,
            "why_top_choice_candidate": f"Top choice for {company}'s {title}: 4.0 GPA ICT (TUAS) + 8+ yrs enterprise infra. Achieved 94% First-Time-Fix rate across 200+ endpoints with automation. Turnkey hire in Finland with permanent EU authorization, 0-day notice, and Supo readiness.",
            "why_top_choice_company": f"Motivation statement for {company}",
            "linkedin_quick_pitch": f"Hi! I'm an IT systems engineer based in Finland (TUAS 4.0 GPA, 8+ yrs infra). I saw the {title} role at {company} and would love to connect. With 0-day notice and permanent EU authorization, I'm eager to discuss how I can bring immediate value to your team!",
            "linkedin_post_draft": f"Excited about the {title} opportunity at {company}!",
            "matched_skills": []
        }

    if folder:
        folder_path = WORKSPACE_DIR / folder
        if folder_path.exists() and folder_path.is_dir():
            try:
                cache_file = folder_path / f"Top_Choice_Pitch_{folder}.json"
                cache_file.write_text(json.dumps(pitch_data, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    pitch_data["cached"] = False
    return jsonify(pitch_data)

@app.route("/api/cover-letter/<folder>/expand", methods=["POST"])
def expand_cover_letter_for_folder(folder):
    """
    Expands the cover letter for a folder into a comprehensive 4-pillar letter (~350–450 words),
    updates the markdown document, and recompiles the vector A4 PDF.
    """
    folder_path = WORKSPACE_DIR / folder
    if not folder_path.exists() or not folder_path.is_dir():
        return jsonify({"error": "Folder not found"}), 404

    role_info = extract_role_info_from_jd(folder_path, folder)
    title = role_info["title"]
    company = role_info["company"]
    location = role_info.get("location", "Finland")

    jd_text = ""
    for f in list(folder_path.glob("*Job_Description*.md")) + list(folder_path.glob("*Analysis*.md")):
        try:
            jd_text = f.read_text(encoding="utf-8", errors="ignore")
            if len(jd_text) > 100:
                break
        except Exception:
            pass

    # Check if posting is in Finnish
    is_finnish = False
    fi_markers = ("hakemus", "tehtävään", "suomi", "työtehtävä", "odotamme", "tarjoamme")
    if any(m in jd_text.lower() for m in fi_markers) or any(m in title.lower() for m in ("kehittäjä", "asiantuntija", "tukihenkilö", "ylläpitäjä")):
        is_finnish = True

    try:
        from scripts.ai_tailor import generate_expanded_cover_letter
    except ImportError:
        try:
            from ai_tailor import generate_expanded_cover_letter
        except ImportError:
            generate_expanded_cover_letter = None

    if not generate_expanded_cover_letter:
        return jsonify({"error": "Cover letter expansion engine unavailable"}), 500

    result = generate_expanded_cover_letter(
        title=title,
        company=company,
        location=location,
        jd_text=jd_text,
        is_finnish=is_finnish
    )

    new_markdown = result["full_markdown"]

    # Write to existing cover letter file or create one
    cl_files = list(folder_path.glob("*Cover_Letter*.md"))
    if cl_files:
        cl_file = cl_files[0]
    else:
        cand = get_candidate_contact_info(WORKSPACE_DIR)
        cand_slug = cand["name"].replace(" ", "_")
        cl_file = folder_path / f"{cand_slug}_Cover_Letter_{folder}.md"

    cl_file.write_text(new_markdown, encoding="utf-8")

    # Recompile PDF
    pdf_file = cl_file.with_suffix(".pdf")
    pdf_generated = False
    try:
        from scripts.export_pdf import generate_pdf
        generate_pdf(str(cl_file), str(pdf_file))
        pdf_generated = True
    except Exception as e:
        print(f"Warning: PDF re-generation failed for expanded cover letter: {e}")

    return jsonify({
        "success": True,
        "content": new_markdown,
        "filename": cl_file.name,
        "pdf_name": pdf_file.name if pdf_generated else None,
        "word_count": result.get("word_count", len(new_markdown.split())),
        "generated_by": result.get("generated_by", "ai")
    })

@app.route("/api/scout/trigger", methods=["POST"])
def trigger_scout_scan():
    """Trigger background job scout script with streaming progress."""
    global scout_process_status
    if scout_process_status["is_running"]:
        return jsonify({"status": "already_running"}), 409
        
    req_data = request.get_json(silent=True) or {}
    mode = req_data.get("mode", "live") # "live" (full web scrape) or "fast" (local feed re-filter)
    
    def run_background_scout(scan_mode):
        global scout_process_status
        scout_process_status["is_running"] = True
        scout_process_status["mode"] = scan_mode
        scout_process_status["stage"] = "Initializing scout environment..."
        scout_process_status["progress_percent"] = 5
        scout_process_status["logs"] = []
        scout_process_status["error"] = None
        
        env = os.environ.copy()
        home_bin = str(Path.home() / ".local" / "bin")
        env["PATH"] = f"{home_bin}:{env.get('PATH', '')}"
        env["PYTHONUNBUFFERED"] = "1"
        
        if scan_mode == "fast":
            cmd = [sys.executable, str(WORKSPACE_DIR / "scripts" / "update_feed.py")]
            scout_process_status["stage"] = "Re-indexing candidate database & updating feed..."
            scout_process_status["progress_percent"] = 25
        else:
            # Full web scout across LinkedIn & Indeed
            cmd = ["bash", str(WORKSPACE_DIR / "scripts" / "scout.sh")]
            scout_process_status["stage"] = "Starting automated IT scout across LinkedIn & Indeed..."
            scout_process_status["progress_percent"] = 10
            
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(WORKSPACE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env
            )
            
            for line in iter(proc.stdout.readline, ''):
                clean = line.strip()
                if not clean:
                    continue
                scout_process_status["logs"].append(clean)
                if len(scout_process_status["logs"]) > 250:
                    scout_process_status["logs"].pop(0)
                    
                # Intelligent progress & stage tracking
                if "Starting Streamlined IT Job Scout" in clean:
                    scout_process_status["stage"] = "Connecting to LinkedIn & Indeed endpoints..."
                    scout_process_status["progress_percent"] = 15
                elif "Searching for:" in clean:
                    m = re.search(r"Searching for: ['\"](.*?)['\"]", clean)
                    q = m.group(1) if m else clean
                    scout_process_status["stage"] = f"Scraping active openings for '{q}'..."
                    scout_process_status["progress_percent"] = min(scout_process_status["progress_percent"] + 6, 75)
                elif "Found" in clean and "postings" in clean:
                    scout_process_status["progress_percent"] = min(scout_process_status["progress_percent"] + 3, 78)
                elif "Applying Strict IT-Role Filtering" in clean or "Filtered for IT-only roles" in clean or "Filtered:" in clean:
                    scout_process_status["stage"] = "Applying strict IT keyword & role taxonomy filters..."
                    scout_process_status["progress_percent"] = 84
                elif "Cross-site deduplication" in clean or "Cross-site deduplicated" in clean:
                    scout_process_status["stage"] = "Cross-site deduplication & language classification..."
                    scout_process_status["progress_percent"] = 92
                elif "Generating Markdown Feed" in clean or "Generating Structured JSON" in clean:
                    scout_process_status["stage"] = "Writing feed & compiling structured mission report..."
                    scout_process_status["progress_percent"] = 96
                elif "Scout Mission Complete" in clean or "Scan complete" in clean or "Successfully updated" in clean:
                    scout_process_status["stage"] = "Finalizing feed & compiling scout report..."
                    scout_process_status["progress_percent"] = 99

            proc.stdout.close()
            code = proc.wait()
            
            if code == 0:
                scout_process_status["stage"] = "Mission Complete! Report Generated."
                scout_process_status["progress_percent"] = 100
                scout_process_status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                scout_process_status["report"] = load_latest_scout_report()
            else:
                scout_process_status["stage"] = f"Scout ended with exit code {code}"
                scout_process_status["error"] = f"Process exited with code {code}"
        except Exception as e:
            scout_process_status["stage"] = f"Scout error: {str(e)}"
            scout_process_status["error"] = str(e)
        finally:
            scout_process_status["is_running"] = False
            
    import threading
    t = threading.Thread(target=run_background_scout, args=(mode,), daemon=True)
    t.start()
    
    return jsonify({"status": "started", "mode": mode})

@app.route("/api/scout/status")
def scout_status():
    """Return live scout status, stage, logs, and latest report."""
    fresh_report = load_latest_scout_report()
    if fresh_report:
        scout_process_status["report"] = fresh_report
    return jsonify(scout_process_status)

@app.route("/api/scout/report")
def scout_report():
    """Return latest structured mission report."""
    report = load_latest_scout_report()
    if not report:
        return jsonify({"error": "No scout report available yet"}), 404
    return jsonify(report)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5500))
    print(f"🚀 Job Hunter Dashboard running on http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
