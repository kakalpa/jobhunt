#!/usr/bin/env python3
"""
job_scout.py - Automated Job Discovery & Pipeline Scout
Scrapes LinkedIn and Indeed for relevant IT, Cybersecurity, and Systems roles
in Finland and EU Remote using JobSpy. Filters strictly for IT jobs, classifies
Finnish language requirements, and deduplicates across multiple platforms.
"""

import sys
import os
import re
import json
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Ensure immediate real-time unbuffered log output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

def log_scout(msg: str):
    """Print timestamped scout log with immediate flush."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# Try importing jobspy
try:
    from jobspy import scrape_jobs
except ImportError:
    log_scout("❌ Error: python-jobspy is not installed.")
    log_scout("Run with: uv run --python 3.12 --with python-jobspy python3 scripts/job_scout.py")
    sys.exit(1)

import pandas as pd

# Import our specialized filtering, language classification & deduplication module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from scripts.job_filters import (
        is_it_job, detect_language_requirement, deduplicate_job_records,
        get_candidate_contact_info, normalize_company, normalize_title
    )
    from scripts.contact_extractor import extract_job_contacts, format_contacts_markdown
except ImportError:
    from job_filters import (
        is_it_job, detect_language_requirement, deduplicate_job_records,
        get_candidate_contact_info, normalize_company, normalize_title
    )
    from contact_extractor import extract_job_contacts, format_contacts_markdown

# Core Candidate Keywords from Base_CV.md for automated scoring
CANDIDATE_KEYWORDS = {
    "Tier 1 (High Match)": [
        "cybersecurity", "security analyst", "soc", "siem", "splunk", "wazuh",
        "secops", "sec ops", "security operations", "security specialist", "security engineer",
        "incident response", "vulnerability", "tenable", "iso 27001", "iso 27005",
        "systems administrator", "system administrator", "it specialist",
        "it support", "field service", "infrastructure engineer", "active directory",
        "azure", "microsoft 365", "entra id", "linux", "red hat", "powershell", "python",
        "junior", "entry level", "trainee", "service desk", "helpdesk", "l1", "l2",
        "data center technician", "hardware technician"
    ],
    "Tier 2 (Adjacent Match)": [
        "devops", "dev ops", "dv ops", "devsecops", "sysops", "cloud ops", "platform engineer",
        "cloud engineer", "network engineer", "firewall", "edr", "ci/cd", "terraform", "ansible",
        "carbon black", "crowdstrike", "intune", "vmware", "proxmox", "docker",
        "kubernetes", "cisco", "iec 62443", "scada", "ot security", "pos", "hardware",
        "break-fix", "troubleshooting", "customer support", "tuki", "it-tuki", "lahituki",
        "dynamics", "erp", "opc ua"
    ]
}

def load_tracked_jobs_index(workspace_dir: Path) -> dict:
    """
    Builds a high-accuracy multi-layer index of all tracked/applied roles from:
    1. pipeline_data.json (canonical URLs, job IDs, titles, companies, statuses)
    2. Workspace directory names (CamelCase split & normalized)
    3. APPLICATIONS_TRACKER.md (markdown tables and URLs)
    """
    tracked = {
        "urls": set(),
        "job_ids": set(),
        "company_titles": set(),
        "companies": set(),
        "folders": [],
        "tracker_text": ""
    }

    # 1. Load pipeline_data.json
    pipeline_file = workspace_dir / "pipeline_data.json"
    if pipeline_file.exists():
        try:
            with open(pipeline_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for folder_key, info in data.items():
                        if not isinstance(info, dict):
                            continue
                        url = (info.get("url") or "").strip().lower()
                        if url:
                            tracked["urls"].add(url)
                            m_li = re.search(r'/view/(\d+)', url)
                            if m_li:
                                tracked["job_ids"].add(m_li.group(1))
                            m_ind = re.search(r'[?&]jk=([a-f0-9]+)', url)
                            if m_ind:
                                tracked["job_ids"].add(m_ind.group(1))

                        comp = info.get("company") or ""
                        tit = info.get("title") or ""
                        if not comp or not tit:
                            parts = folder_key.split("_", 1)
                            if not comp and parts:
                                comp = re.sub(r'([a-z])([A-Z])', r'\1 \2', parts[0])
                            if not tit and len(parts) > 1:
                                tit = parts[1].replace("_", " ")

                        comp_n = normalize_company(comp)
                        tit_n = normalize_title(tit)
                        if comp_n and tit_n:
                            tracked["company_titles"].add((comp_n, tit_n))
                        if comp_n:
                            tracked["companies"].add(comp_n)
        except Exception as e:
            log_scout(f"⚠️ Notice reading pipeline_data.json: {e}")

    # 2. Scan workspace directory names
    for item in workspace_dir.iterdir():
        if item.is_dir() and not item.name.startswith(('.', 'scripts', 'dashboard', 'static', 'templates', 'venv', 'brain')):
            name_spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', item.name).replace('_', ' ').lower()
            name_alphanumeric = re.sub(r'[^a-z0-9]', '', item.name.lower())
            tracked["folders"].append((name_spaced, name_alphanumeric))

    # 3. Scan APPLICATIONS_TRACKER.md
    tracker_path = workspace_dir / "APPLICATIONS_TRACKER.md"
    if tracker_path.exists():
        try:
            content = tracker_path.read_text(encoding="utf-8", errors="ignore").lower()
            tracked["tracker_text"] = content
            for u in re.findall(r'https?://[^\s\)\"\'>]+', content):
                tracked["urls"].add(u.rstrip("/").lower())
        except Exception:
            pass

    return tracked

def get_already_applied_titles(workspace_dir: Path):
    """Backwards-compatible wrapper."""
    return load_tracked_jobs_index(workspace_dir)

def is_job_tracked(job: dict, tracked: dict) -> bool:
    """Accurately checks whether a discovered job is already tracked or applied."""
    url = (job.get("job_url") or job.get("url") or "").strip().lower()

    # 1. Exact or canonical URL match
    if url:
        if url in tracked["urls"] or url.rstrip("/") in tracked["urls"]:
            return True
        m_li = re.search(r'/view/(\d+)', url)
        if m_li and m_li.group(1) in tracked["job_ids"]:
            return True
        m_ind = re.search(r'[?&]jk=([a-f0-9]+)', url)
        if m_ind and m_ind.group(1) in tracked["job_ids"]:
            return True

    company = str(job.get("company", ""))
    title = str(job.get("title", ""))
    comp_n = normalize_company(company)
    tit_n = normalize_title(title)

    # 2. Canonical (Company, Title) pair match in pipeline
    if (comp_n, tit_n) in tracked["company_titles"]:
        return True

    # 3. Same company with overlapping title in pipeline
    if comp_n and comp_n in tracked["companies"]:
        for c_t, t_t in tracked["company_titles"]:
            if c_t == comp_n or (comp_n in c_t) or (c_t in comp_n):
                if tit_n == t_t or tit_n in t_t or t_t in tit_n:
                    return True
                t_words = set(re.findall(r'[a-z]{3,}', tit_n))
                tracked_words = set(re.findall(r'[a-z]{3,}', t_t))
                if t_words and tracked_words and len(t_words & tracked_words) >= 2:
                    return True

    # 4. Check against workspace directory names
    raw_comp_alpha = re.sub(r'[^a-z0-9]', '', company.lower())
    for name_spaced, name_alpha in tracked["folders"]:
        if (comp_n and comp_n in name_alpha) or (raw_comp_alpha and raw_comp_alpha in name_alpha):
            key_tokens = [w for w in re.findall(r'[a-z]{3,}', title.lower()) if w not in {"the", "and", "for", "with", "junior", "senior", "finland"}]
            matched_tokens = sum(1 for w in key_tokens if w in name_spaced or w in name_alpha)
            if matched_tokens >= 2 or (len(key_tokens) == 1 and matched_tokens == 1):
                return True

    # 5. Check APPLICATIONS_TRACKER.md text
    if tracked["tracker_text"]:
        comp_lower = company.lower().strip()
        if comp_lower in tracked["tracker_text"] and len(comp_lower) > 3:
            key_tokens = [w for w in re.findall(r'[a-z]{3,}', title.lower()) if w not in {"the", "and", "for", "with", "junior", "senior", "finland"}]
            matched_tokens = sum(1 for w in key_tokens if w in tracked["tracker_text"])
            if matched_tokens >= 2:
                return True

    return False

def calculate_match_score(title: str, description: str) -> int:
    """Calculates approximate keyword match percentage against candidate profile."""
    text = f"{title} {description}".lower()
    score = 65 # Base score for matching search query
    
    tier1_hits = sum(1 for kw in CANDIDATE_KEYWORDS["Tier 1 (High Match)"] if kw in text)
    tier2_hits = sum(1 for kw in CANDIDATE_KEYWORDS["Tier 2 (Adjacent Match)"] if kw in text)
    
    score += min(tier1_hits * 4, 24)
    score += min(tier2_hits * 2, 8)
    
    return min(score, 98)

def scrape_duunitori(query: str, location: str = "Finland", limit: int = 15) -> list:
    """Scrapes Duunitori for Finnish local IT job postings."""
    import urllib.request
    import urllib.parse
    import html as html_lib
    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://duunitori.fi/tyopaikat?haku={encoded_query}"
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    jobs = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            page_html = r.read().decode("utf-8", errors="ignore")
        pattern = r'<a[^>]+data-company=\"([^\"]*)\"[^>]*href=\"(/tyopaikat/tyo/[^\"]+)\"[^>]*>(.*?)</a>'
        matches = re.findall(pattern, page_html, re.DOTALL)
        for comp, link, title in matches:
            if "lisaa_suosikkeihin" in link:
                continue
            clean_title = html_lib.unescape(re.sub(r'<[^>]+>', '', title).strip())
            clean_comp = html_lib.unescape(comp.strip())
            jobs.append({
                "title": clean_title,
                "company": clean_comp,
                "location": "Finland",
                "job_url": f"https://duunitori.fi{link}",
                "site": "duunitori",
                "date_posted": "Recently",
                "description": f"{clean_title} at {clean_comp} via Duunitori."
            })
            if len(jobs) >= limit:
                break
    except Exception as e:
        log_scout(f"   ⚠️ [Duunitori] notice: {e}")
    return jobs

def scrape_arbeitnow(query: str, limit: int = 15) -> list:
    """Scrapes Arbeitnow API for European English-speaking and remote IT jobs."""
    import urllib.request
    import urllib.parse
    import html as html_lib
    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://www.arbeitnow.com/api/job-board-api?search={encoded_query}"
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) JobHuntScout/1.0"}
    jobs = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8", errors="ignore"))
        data_items = payload.get("data", [])
        for item in data_items:
            title = html_lib.unescape(item.get("title", "").strip())
            comp = html_lib.unescape(item.get("company_name", "").strip())
            job_url = item.get("url", "")
            loc = item.get("location", "EU Remote")
            desc_raw = re.sub(r'<[^>]+>', ' ', item.get("description", ""))
            clean_desc = html_lib.unescape(desc_raw).strip()
            
            jobs.append({
                "title": title,
                "company": comp,
                "location": f"{loc} (Remote)" if item.get("remote") else loc,
                "job_url": job_url,
                "site": "arbeitnow",
                "date_posted": "Recently",
                "description": clean_desc[:2000]
            })
            if len(jobs) >= limit:
                break
    except Exception as e:
        log_scout(f"   ⚠️ [Arbeitnow API] notice: {e}")
    return jobs

def scrape_jobly(query: str, limit: int = 15) -> list:
    """Scrapes Jobly.fi for Finnish national and enterprise IT job postings."""
    import urllib.request
    import urllib.parse
    import html as html_lib
    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://www.jobly.fi/tyopaikat?search={encoded_query}"
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    jobs = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
        cards = re.findall(r"<article[^>]*>(.*?)</article>", html, re.DOTALL)
        for c in cards:
            link_m = re.search(r"href=\"(https://www\.jobly\.fi/tyopaikka/[^\"]+)\"", c)
            if not link_m:
                continue
            job_url = link_m.group(1)
            title_m = re.search(r"<h[23][^>]*>(.*?)</h[23]>", c, re.DOTALL)
            clean_title = html_lib.unescape(re.sub(r"<[^>]+>", "", title_m.group(1)).strip()) if title_m else "IT Opportunity"
            
            comp_m = re.search(r"class=\"recruiter-company-profile-job-organization\"[^>]*>.*?<a[^>]*>(.*?)</a>", c, re.DOTALL)
            if not comp_m:
                comp_m = re.search(r"class=\"[^\"]*company[^\"]*\"[^>]*>(.*?)<", c, re.DOTALL)
            raw_comp = html_lib.unescape(re.sub(r"<[^>]+>", "", comp_m.group(1)).strip()) if comp_m else ""
            if not raw_comp or "tallenna" in raw_comp.lower() or len(raw_comp) < 2:
                parts = [p.strip() for p in clean_title.split(",") if p.strip()]
                clean_comp = parts[1] if len(parts) >= 2 else "Employer via Jobly"
            else:
                clean_comp = raw_comp
            
            loc_m = re.search(r"class=\"location\"[^>]*>.*?<span>(.*?)</span>", c, re.DOTALL)
            if not loc_m:
                loc_m = re.search(r"class=\"[^\"]*location[^\"]*\"[^>]*>(.*?)<", c, re.DOTALL)
            clean_loc = html_lib.unescape(re.sub(r"<[^>]+>", "", loc_m.group(1)).strip()) if loc_m else "Finland"
            
            jobs.append({
                "title": clean_title,
                "company": clean_comp,
                "location": clean_loc,
                "job_url": job_url,
                "site": "jobly",
                "date_posted": "Recently",
                "description": f"{clean_title} at {clean_comp} in {clean_loc} via Jobly.fi."
            })
            if len(jobs) >= limit:
                break
    except Exception as e:
        log_scout(f"   ⚠️ [Jobly.fi] notice: {e}")
    return jobs

def scrape_thehub(query: str, limit: int = 15) -> list:
    """Scrapes The Hub (thehub.io) for Finnish tech startups & scaleup IT roles."""
    import urllib.request
    import urllib.parse
    import html as html_lib
    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://thehub.io/jobs?countryCode=FI&search={encoded_query}"
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    jobs = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
        cards = re.findall(r"(<div class=\"card-job-find-list[^\"]*\">.*?</a>\s*</div>)", html, re.DOTALL)
        for c in cards:
            link_m = re.search(r"href=\"(/jobs/[a-f0-9]+)\"", c)
            if not link_m:
                continue
            job_url = f"https://thehub.io{link_m.group(1)}"
            lines = [l.strip() for l in re.sub(r"<[^>]+>", "\n", c).splitlines() if l.strip()]
            if not lines:
                continue
            title = html_lib.unescape(lines[0])
            comp = html_lib.unescape(lines[1]) if len(lines) > 1 else "Startup via The Hub"
            loc = html_lib.unescape(lines[2]) if len(lines) > 2 else "Finland"
            
            jobs.append({
                "title": title,
                "company": comp,
                "location": f"{loc}, Finland" if "finland" not in loc.lower() else loc,
                "job_url": job_url,
                "site": "thehub",
                "date_posted": "Recently",
                "description": f"{title} at {comp} ({loc}) via The Hub Finland (Nordic Startup Jobs)."
            })
            if len(jobs) >= limit:
                break
    except Exception as e:
        log_scout(f"   ⚠️ [The Hub] notice: {e}")
    return jobs

def scrape_weworkremotely(query: str, limit: int = 15) -> list:
    """Scrapes WeWorkRemotely for global & European remote DevOps, Cloud, and Security jobs."""
    import urllib.request
    import html as html_lib
    url = "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"
    headers = {"User-Agent": "JobHuntScout/1.0 (Mozilla/5.0)"}
    jobs = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            xml = r.read().decode("utf-8", errors="ignore")
        items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
        q_tokens = [w for w in query.lower().split() if len(w) > 2]
        for it in items:
            t_m = re.search(r"<title>(.*?)</title>", it)
            l_m = re.search(r"<link>(.*?)</link>", it)
            d_m = re.search(r"<description>(.*?)</description>", it, re.DOTALL)
            if not t_m or not l_m:
                continue
            raw_title = html_lib.unescape(t_m.group(1)).strip()
            job_url = html_lib.unescape(l_m.group(1)).strip()
            desc = html_lib.unescape(re.sub(r"<[^>]+>", " ", d_m.group(1))).strip() if d_m else ""
            
            parts = raw_title.split(":", 1)
            comp = parts[0].strip() if len(parts) > 1 else "Remote Tech Employer"
            title = parts[1].strip() if len(parts) > 1 else raw_title
            
            combined = f"{title} {desc}".lower()
            if q_tokens and not any(tok in combined for tok in q_tokens):
                continue
                
            jobs.append({
                "title": title,
                "company": comp,
                "location": "Worldwide / EU Remote",
                "job_url": job_url,
                "site": "weworkremotely",
                "date_posted": "Recently",
                "description": f"{title} at {comp} (Remote). {desc[:600]}"
            })
            if len(jobs) >= limit:
                break
    except Exception as e:
        log_scout(f"   ⚠️ [WeWorkRemotely] notice: {e}")
    return jobs

def acquire_scout_lock(workspace_dir: Path):
    lock_path = workspace_dir / ".scout.lock"
    try:
        import fcntl
        fd = open(lock_path, "w")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (IOError, OSError):
        return None
    except Exception:
        return None

def release_scout_lock(fd, workspace_dir: Path = None):
    if fd:
        try:
            import fcntl
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
            if workspace_dir:
                lock_file = workspace_dir / ".scout.lock"
                if lock_file.exists():
                    lock_file.unlink(missing_ok=True)
        except Exception:
            pass

def run_scout(queries: list, location: str, hours: int, limit: int, remote_only: bool, sites: list):
    start_time = time.time()
    workspace_dir = Path(os.environ.get("WORKSPACE_DIR", Path(__file__).resolve().parent.parent)).resolve()

    lock_fd = acquire_scout_lock(workspace_dir)
    if lock_fd is None:
        log_scout("⚠️ Another scout discovery run is already in progress. Exiting cleanly.")
        return None

    tracked_index = load_tracked_jobs_index(workspace_dir)
    
    # Supported engines in JobSpy
    supported_sites = ["linkedin", "indeed", "google", "glassdoor"]
    valid_sites = [s for s in sites if s in supported_sites]
    if "finland" in location.lower():
        country_indeed = "finland"
    else:
        country_indeed = "usa"
        
    if not valid_sites:
        valid_sites = ["linkedin", "indeed", "google", "glassdoor"]
        
    log_scout("==================================================================")
    log_scout(f"🚀 Starting Streamlined IT Job Scout at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_scout(f"📍 Location: {location} | Remote Only: {remote_only} | Lookback: {hours}h")
    log_scout(f"🌐 Platforms: {', '.join(valid_sites)} + Duunitori + Jobly + The Hub + Arbeitnow + WeWorkRemotely | Queries: {len(queries)}")
    log_scout("==================================================================")
    
    all_jobs = []
    
    for idx, query in enumerate(queries, 1):
        pct = int((idx / len(queries)) * 100)
        log_scout(f"\n🔍 [{idx}/{len(queries)} - {pct}%] Searching for: '{query}' across {', '.join(valid_sites)}, Duunitori, Jobly, The Hub, Arbeitnow & Remote Tech...")
        
        query_start = time.time()
        query_jobs_count = 0
        
        for site in valid_sites:
            log_scout(f"   ⏳ Querying [{site.capitalize()}] for '{query}'...")
            try:
                is_site_remote = remote_only or (site == "glassdoor" and ("remote" in query.lower() or remote_only))
                site_loc = None if (site == "glassdoor" and is_site_remote) else location
                jobs: pd.DataFrame = scrape_jobs(
                    site_name=[site],
                    search_term=query,
                    location=site_loc,
                    results_wanted=limit,
                    hours_old=hours,
                    country_indeed=country_indeed,
                    is_remote=is_site_remote,
                    linkedin_fetch_description=True
                )
                
                if jobs is not None and not jobs.empty:
                    log_scout(f"   ↳ [{site.capitalize()}] Found {len(jobs)} postings:")
                    # Preview discovered titles
                    for _, row in jobs.head(3).iterrows():
                        t_title = str(row.get("title", "N/A")).strip()
                        t_comp = str(row.get("company", "N/A")).strip()
                        t_loc = str(row.get("location", location)).strip()
                        log_scout(f"      • {t_title} @ {t_comp} ({t_loc})")
                    if len(jobs) > 3:
                        log_scout(f"      ... and {len(jobs) - 3} more postings")
                    all_jobs.append(jobs)
                    query_jobs_count += len(jobs)
                else:
                    log_scout(f"   ↳ [{site.capitalize()}] 0 postings found")
            except Exception as e:
                log_scout(f"   ⚠️ [{site.capitalize()}] notice: {e}")

        # Query Duunitori for Finnish local opportunities
        if "finland" in location.lower() or "duunitori" in sites:
            log_scout(f"   ⏳ Querying [Duunitori] for '{query}'...")
            try:
                duuni_list = scrape_duunitori(query, location, limit=limit)
                if duuni_list:
                    log_scout(f"   ↳ [Duunitori] Found {len(duuni_list)} postings:")
                    for d_job in duuni_list[:3]:
                        log_scout(f"      • {d_job['title']} @ {d_job['company']}")
                    if len(duuni_list) > 3:
                        log_scout(f"      ... and {len(duuni_list) - 3} more postings")
                    all_jobs.append(pd.DataFrame(duuni_list))
                    query_jobs_count += len(duuni_list)
                else:
                    log_scout(f"   ↳ [Duunitori] 0 postings found")
            except Exception as e:
                log_scout(f"   ⚠️ [Duunitori] notice: {e}")

        # Query Jobly.fi for Finnish enterprise & consultancy opportunities
        if "finland" in location.lower() or "jobly" in sites:
            log_scout(f"   ⏳ Querying [Jobly.fi] for '{query}'...")
            try:
                jobly_list = scrape_jobly(query, limit=limit)
                if jobly_list:
                    log_scout(f"   ↳ [Jobly.fi] Found {len(jobly_list)} postings:")
                    for j_job in jobly_list[:3]:
                        log_scout(f"      • {j_job['title']} @ {j_job['company']}")
                    if len(jobly_list) > 3:
                        log_scout(f"      ... and {len(jobly_list) - 3} more postings")
                    all_jobs.append(pd.DataFrame(jobly_list))
                    query_jobs_count += len(jobly_list)
                else:
                    log_scout(f"   ↳ [Jobly.fi] 0 postings found")
            except Exception as e:
                log_scout(f"   ⚠️ [Jobly.fi] notice: {e}")

        # Query The Hub for Finnish tech startup & scaleup opportunities
        if "finland" in location.lower() or "thehub" in sites:
            log_scout(f"   ⏳ Querying [The Hub (Nordic Startups)] for '{query}'...")
            try:
                hub_list = scrape_thehub(query, limit=limit)
                if hub_list:
                    log_scout(f"   ↳ [The Hub] Found {len(hub_list)} postings:")
                    for h_job in hub_list[:3]:
                        log_scout(f"      • {h_job['title']} @ {h_job['company']}")
                    if len(hub_list) > 3:
                        log_scout(f"      ... and {len(hub_list) - 3} more postings")
                    all_jobs.append(pd.DataFrame(hub_list))
                    query_jobs_count += len(hub_list)
                else:
                    log_scout(f"   ↳ [The Hub] 0 postings found")
            except Exception as e:
                log_scout(f"   ⚠️ [The Hub] notice: {e}")

        # Query Arbeitnow for European English-speaking & Remote IT opportunities
        log_scout(f"   ⏳ Querying [Arbeitnow EU API] for '{query}'...")
        try:
            arbeit_list = scrape_arbeitnow(query, limit=limit)
            if arbeit_list:
                log_scout(f"   ↳ [Arbeitnow] Found {len(arbeit_list)} postings:")
                for a_job in arbeit_list[:3]:
                    log_scout(f"      • {a_job['title']} @ {a_job['company']} ({a_job['location']})")
                if len(arbeit_list) > 3:
                    log_scout(f"      ... and {len(arbeit_list) - 3} more postings")
                all_jobs.append(pd.DataFrame(arbeit_list))
                query_jobs_count += len(arbeit_list)
            else:
                log_scout(f"   ↳ [Arbeitnow] 0 postings found")
        except Exception as e:
            log_scout(f"   ⚠️ [Arbeitnow] notice: {e}")

        # Query WeWorkRemotely for global & European remote tech / DevOps / Security roles
        if remote_only or "remote" in location.lower() or "wwr" in sites or "remote" in query.lower():
            log_scout(f"   ⏳ Querying [WeWorkRemotely (Remote Tech)] for '{query}'...")
            try:
                wwr_list = scrape_weworkremotely(query, limit=limit)
                if wwr_list:
                    log_scout(f"   ↳ [WeWorkRemotely] Found {len(wwr_list)} postings:")
                    for w_job in wwr_list[:3]:
                        log_scout(f"      • {w_job['title']} @ {w_job['company']} ({w_job['location']})")
                    if len(wwr_list) > 3:
                        log_scout(f"      ... and {len(wwr_list) - 3} more postings")
                    all_jobs.append(pd.DataFrame(wwr_list))
                    query_jobs_count += len(wwr_list)
                else:
                    log_scout(f"   ↳ [WeWorkRemotely] 0 postings found")
            except Exception as e:
                log_scout(f"   ⚠️ [WeWorkRemotely] notice: {e}")
                
        cumulative_count = sum(len(df) for df in all_jobs)
        log_scout(f"   ✓ Query '{query}' completed in {time.time() - query_start:.1f}s (+{query_jobs_count} postings, total: {cumulative_count})")
            
    if not all_jobs:
        log_scout("\n❌ No jobs found across the specified criteria. Try widening the hours or search terms.")
        release_scout_lock(lock_fd, workspace_dir)
        return None
        
    combined_df = pd.concat(all_jobs, ignore_index=True)
    raw_count = len(combined_df)
    
    # 1. Convert to dict records for processing
    raw_records = combined_df.to_dict(orient="records")
    
    # 2. Strict IT-Only Filtering
    log_scout(f"\n🎯 Applying Strict IT-Role Filtering to {raw_count} raw postings...")
    it_records = []
    excluded_samples = []
    for r in raw_records:
        title = str(r.get("title", ""))
        desc = str(r.get("description", ""))
        if is_it_job(title, desc):
            it_records.append(r)
        elif len(excluded_samples) < 3:
            excluded_samples.append(f"{title} @ {r.get('company', 'Unknown')}")
            
    log_scout(f"   • Retained IT Roles: {len(it_records)} of {raw_count} raw postings")
    log_scout(f"   • Excluded Non-IT:  {raw_count - len(it_records)} postings")
    if excluded_samples:
        log_scout(f"   • Sample Excluded:  {', '.join(excluded_samples)}")
    
    # 3. Language Requirement Classification & Scoring
    log_scout("\n🧠 Calculating Profile Match Scores & Finnish Language Requirements...")
    scored_records = []
    for r in it_records:
        title = str(r.get("title", "Unknown Title"))
        company = str(r.get("company", "Unknown Company"))
        job_location = str(r.get("location", location))
        job_url = str(r.get("job_url", ""))
        date_posted = str(r.get("date_posted", "Recently"))
        site = str(r.get("site", "Web"))
        description = str(r.get("description", ""))
        
        lang_info = detect_language_requirement(title, description)
        match_score = calculate_match_score(title, description)
        contacts = extract_job_contacts(description, title, company)
        
        # Check if already tracked/applied
        is_already_tracked = is_job_tracked(r, tracked_index)
        
        scored_records.append({
            "title": title,
            "company": company,
            "location": job_location,
            "site": site,
            "job_url": job_url,
            "url": job_url,
            "date": date_posted,
            "match_score": match_score,
            "already_applied": is_already_tracked,
            "language_tag": lang_info["tag"],
            "language_badge": lang_info["badge"],
            "description": description,
            "description_snippet": description[:300].replace('\n', ' ') + "..." if description else "N/A",
            "contacts": contacts
        })
        
    # 4. Cross-Site Deduplication Engine
    deduped_records = deduplicate_job_records(scored_records)
    log_scout(f"✨ Cross-site deduplication merged multi-platform postings: {len(scored_records)} -> {len(deduped_records)} unique IT roles")
    
    # 5. Sort by match score descending
    deduped_records.sort(key=lambda x: x["match_score"], reverse=True)
    
    # Preview top matches
    log_scout("\n🏆 Top Prioritized Opportunities for Candidate:")
    for idx, job in enumerate(deduped_records[:5], 1):
        status_str = "⚠️ Tracked" if job["already_applied"] else "⭐ NEW"
        log_scout(f"   #{idx} [{job['match_score']}% Match] {job['title']} — {job['company']} ({job.get('platform_display', job['site'])}) [{job['language_badge']}] [{status_str}]")
    
    # 6. Generate Markdown Report
    output_md = workspace_dir / "JOB_SCOUT_FEED.md"
    log_scout(f"\n💾 Generating Markdown Feed: {output_md}...")
    
    try:
        import zoneinfo
        fi_tz = zoneinfo.ZoneInfo("Europe/Helsinki")
        now_fi = datetime.now(fi_tz)
        timestamp_display = f"{now_fi.strftime('%Y-%m-%d %H:%M:%S')} {now_fi.tzname() or 'EEST'}"
        iso_stamp = now_fi.isoformat()
    except Exception:
        now_local = datetime.now().astimezone()
        timestamp_display = f"{now_local.strftime('%Y-%m-%d %H:%M:%S')} {now_local.tzname() or 'EEST'}"
        iso_stamp = now_local.isoformat()

    with open(output_md, "w", encoding="utf-8") as f:
        f.write(f"# Automated IT Job Scout Feed\n\n")
        f.write(f"**Last Scanned:** {timestamp_display}  \n")
        f.write(f"**Target Location:** {location} | **Remote Filter:** {remote_only} | **Lookback:** {hours} hours  \n")
        f.write(f"**Discovered Postings:** {len(deduped_records)} unique IT roles (Cross-Site Deduplicated)  \n\n")
        f.write("---\n\n")
        cand = get_candidate_contact_info(workspace_dir)
        f.write(f"## 🎯 Prioritized IT Opportunities for {cand['name']}\n\n")
        f.write("| Match Score | Role Title | Company | Location | Language Requirement | Platform | Status | Direct Link |\n")
        f.write("| :---: | :--- | :--- | :--- | :---: | :---: | :---: | :--- |\n")
        
        for job in deduped_records:
            status_badge = "⚠️ Already Tracked" if job["already_applied"] else "⭐ **NEW**"
            platform_str = job.get("platform_display", str(job.get("site", "Web")).capitalize())
            f.write(f"| **{job['match_score']}%** | {job['title']} | **{job['company']}** | {job['location']} | {job['language_badge']} | {platform_str} | {status_badge} | [Apply / View]({job['url']}) |\n")
            
        f.write("\n---\n\n")
        f.write("## 📋 Top Recommended IT Jobs (Ready to Run Workflow)\n\n")
        
        for idx, job in enumerate(deduped_records[:8], 1):
            platform_str = job.get("platform_display", str(job.get("site", "Web")).capitalize())
            f.write(f"### {idx}. {job['title']} — {job['company']}\n")
            f.write(f"- **Estimated Match:** {job['match_score']}% | **Platform:** {platform_str} | **Location:** {job['location']}\n")
            f.write(f"- **Language Requirement:** {job['language_badge']}\n")
            f.write(f"- **Direct Link:** {job['url']}\n")
            contacts_info = job.get("contacts", {})
            if contacts_info.get("has_contacts"):
                c_details = []
                if contacts_info.get("primary_name"):
                    c_name = contacts_info['primary_name']
                    if contacts_info.get("primary_title"):
                        c_name += f" ({contacts_info['primary_title']})"
                    c_details.append(f"**Contact:** {c_name}")
                if contacts_info.get("primary_email"):
                    c_details.append(f"**Email:** {contacts_info['primary_email']}")
                if contacts_info.get("primary_phone"):
                    c_details.append(f"**Phone:** `{contacts_info['primary_phone']}`")
                if contacts_info.get("calling_hours"):
                    c_details.append(f"**Calling Hours:** *{contacts_info['calling_hours']}*")
                f.write(f"- **Discovered Contacts:** {' | '.join(c_details)}\n")
            if contacts_info.get("linkedin_search_url"):
                f.write(f"- **Recruiter Search:** [Search Recruiters on LinkedIn]({contacts_info['linkedin_search_url']})\n")
            f.write(f"- **Summary:** {job['description_snippet']}\n")
            f.write(f"- **Instant Workflow Command:** Tell Antigravity:\n")
            f.write(f"  > `do the workflow for this: {job['url']}`\n\n")
            
    # 6. Save Structured JSON Report
    output_json = workspace_dir / "scout_latest_report.json"
    log_scout(f"💾 Generating Structured JSON Report: {output_json}...")
    report_data = {
        "last_scanned": timestamp_display,
        "last_scanned_iso": iso_stamp,
        "location": location,
        "raw_count": raw_count,
        "it_count": len(it_records),
        "deduped_count": len(deduped_records),
        "high_match_count": sum(1 for j in deduped_records if j["match_score"] >= 80),
        "new_count": sum(1 for j in deduped_records if not j["already_applied"]),
        "jobs": [
            {
                "title": j["title"],
                "company": j["company"],
                "location": j["location"],
                "platform": j.get("platform_display", str(j.get("site", "Web")).capitalize()),
                "match_score": j["match_score"],
                "language_badge": j["language_badge"],
                "language_tag": j.get("language_tag", ""),
                "already_applied": j["already_applied"],
                "url": j["url"],
                "description_snippet": j.get("description_snippet", ""),
                "contacts": j.get("contacts", {})
            }
            for j in deduped_records
        ]
    }
    with open(output_json, "w", encoding="utf-8") as jf:
        json.dump(report_data, jf, indent=2, ensure_ascii=False)

    # Notifications for new high-match roles
    new_high = [j for j in deduped_records if j["match_score"] >= 80 and not j["already_applied"]]
    if new_high:
        top_role = new_high[0]
        # 1. Desktop notification (host OS)
        try:
            import shutil
            import subprocess
            if shutil.which("notify-send"):
                subprocess.run(
                    ["notify-send", "-u", "normal", "-i", "briefcase",
                     f"🎯 IT Scout: New High Match ({top_role['match_score']}%)",
                     f"{top_role['title']} — {top_role['company']} ({len(new_high)} new roles)"],
                    check=False, timeout=3
                )
        except Exception:
            pass

    # 2. Telegram Notifications (Only send TRULY NEW, unseen postings)
    try:
        try:
            from scripts.telegram_notifier import (
                notify_new_job_opportunity,
                notify_scout_scan_summary,
                load_sent_job_keys,
                get_job_dedup_keys
            )
        except ImportError:
            from telegram_notifier import (
                notify_new_job_opportunity,
                notify_scout_scan_summary,
                load_sent_job_keys,
                get_job_dedup_keys
            )

        sent_keys = load_sent_job_keys()

        # Filter for roles that have NOT been applied to AND NOT previously alerted to Telegram
        unnotified_new_high = []
        for j in new_high:
            j_keys = get_job_dedup_keys(j)
            if not any(k in sent_keys for k in j_keys):
                unnotified_new_high.append(j)

        if unnotified_new_high:
            log_scout(f"📱 Dispatching Telegram alerts for {len(unnotified_new_high)} new high-match IT roles...")
            for j in unnotified_new_high[:3]:
                notify_new_job_opportunity(j)

            # Notify scan summary only when new roles were discovered
            notify_scout_scan_summary(
                new_roles_count=len(unnotified_new_high),
                high_match_count=sum(1 for j in unnotified_new_high if j["match_score"] >= 80),
                total_unique=report_data["deduped_count"]
            )
        else:
            log_scout("📱 Telegram alerts: No new unalerted postings to send (all active roles previously notified).")
    except Exception as e:
        log_scout(f"⚠️ Telegram notification notice: {e}")

    # 3. Automated Storage Retention Management
    try:
        try:
            from scripts.retention_manager import clean_expired_postings, get_retention_config
        except ImportError:
            from retention_manager import clean_expired_postings, get_retention_config

        retention_cfg = get_retention_config()
        if retention_cfg["enabled"]:
            ret_res = clean_expired_postings(
                days=retention_cfg["days"],
                protect_statuses=retention_cfg["protect_statuses"],
                mode=retention_cfg["mode"],
                dry_run=False
            )
            if ret_res["removed_count"] > 0:
                log_scout(f"🧹 Retention policy cleaned {ret_res['removed_count']} expired posting packages (freed {ret_res['freed_mb']} MB)")
    except Exception as e:
        log_scout(f"⚠️ Retention cleanup notice: {e}")

    elapsed = time.time() - start_time
    log_scout("==================================================================")
    log_scout(f"✅ Scout Mission Complete in {elapsed:.1f}s!")
    log_scout(f"📊 Discovered {len(deduped_records)} unique IT roles ({sum(1 for j in deduped_records if j['match_score'] >= 80)} high matches)")
    log_scout(f"📁 Live feed saved to: {output_md}")
    release_scout_lock(lock_fd, workspace_dir)
    return output_md

def main():
    env_queries = os.environ.get("SCOUT_QUERIES", "").strip()
    if env_queries:
        default_queries = [q.strip() for q in env_queries.split(",") if q.strip()]
    else:
        default_queries = [
            "Junior IT",
            "Junior Security",
            "Junior Systems Administrator",
            "IT Support Specialist",
            "Service Desk Analyst",
            "Data Center Technician",
            "Field Service Technician",
            "SOC Analyst",
            "IT Specialist",
            "Cybersecurity",
            "IT Trainee",
            "System Administrator",
            "Sec Ops",
            "Dev ops",
            "Junior Sec Ops",
            "DV Ops"
        ]

    parser = argparse.ArgumentParser(description="Automated IT Job Discovery Scout for Finland & EU Remote")
    parser.add_argument("--queries", "-q", nargs="+", default=default_queries, help="Search terms to query")
    parser.add_argument("--location", "-l", default=os.environ.get("SCOUT_LOCATION", "Finland"), help="Target location (default: Finland)")
    parser.add_argument("--hours", "-t", type=int, default=int(os.environ.get("SCOUT_LOOKBACK_HOURS", 168)), help="Hours old to search (default: 168h / 7 days)")
    parser.add_argument("--limit", "-n", type=int, default=int(os.environ.get("SCOUT_LIMIT_PER_QUERY", 10)), help="Results wanted per query per site (default: 10)")
    parser.add_argument("--remote", action="store_true", help="Filter for remote jobs only")
    parser.add_argument("--sites", nargs="+", default=["linkedin", "indeed", "google", "glassdoor"], help="Sites to scrape (linkedin, indeed, google, glassdoor)")
    
    args = parser.parse_args()
    
    run_scout(
        queries=args.queries,
        location=args.location,
        hours=args.hours,
        limit=args.limit,
        remote_only=args.remote,
        sites=args.sites
    )

if __name__ == "__main__":
    main()
