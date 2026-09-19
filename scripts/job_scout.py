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
import argparse
from datetime import datetime
from pathlib import Path

# Try importing jobspy
try:
    from jobspy import scrape_jobs
except ImportError:
    print("❌ Error: python-jobspy is not installed.")
    print("Run with: uv run --python 3.12 --with python-jobspy python3 scripts/job_scout.py")
    sys.exit(1)

import pandas as pd

# Import our specialized filtering, language classification & deduplication module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from scripts.job_filters import is_it_job, detect_language_requirement, deduplicate_job_records, get_candidate_contact_info
except ImportError:
    from job_filters import is_it_job, detect_language_requirement, deduplicate_job_records, get_candidate_contact_info

# Core Candidate Keywords from Base_CV.md for automated scoring
CANDIDATE_KEYWORDS = {
    "Tier 1 (High Match)": [
        "cybersecurity", "security analyst", "soc", "siem", "splunk", "wazuh",
        "incident response", "vulnerability", "tenable", "iso 27001", "iso 27005",
        "systems administrator", "system administrator", "it specialist",
        "it support", "field service", "infrastructure engineer", "active directory",
        "azure", "microsoft 365", "entra id", "linux", "red hat", "powershell", "python",
        "junior", "entry level", "trainee", "service desk", "helpdesk", "l1", "l2",
        "data center technician", "hardware technician"
    ],
    "Tier 2 (Adjacent Match)": [
        "devops", "cloud engineer", "network engineer", "firewall", "edr",
        "carbon black", "crowdstrike", "intune", "vmware", "proxmox", "docker",
        "kubernetes", "cisco", "iec 62443", "scada", "ot security", "pos", "hardware",
        "break-fix", "troubleshooting", "customer support", "tuki", "it-tuki", "lahituki",
        "dynamics", "erp", "opc ua"
    ]
}

def get_already_applied_titles(workspace_dir: Path) -> set:
    """Scan APPLICATIONS_TRACKER.md and workspace directories to avoid duplicate alerts."""
    applied_names = set()
    
    # 1. Scan directory names
    for item in workspace_dir.iterdir():
        if item.is_dir() and not item.name.startswith(('.', 'scripts', 'dashboard', 'static', 'templates', 'venv')):
            clean_name = item.name.lower().replace('_', ' ')
            applied_names.add(clean_name)
            
    # 2. Scan tracker markdown
    tracker_path = workspace_dir / "APPLICATIONS_TRACKER.md"
    if tracker_path.exists():
        content = tracker_path.read_text(encoding="utf-8", errors="ignore").lower()
        applied_names.add(content)
        
    return applied_names

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
        print(f"   ⚠️ [Duunitori] notice: {e}")
    return jobs

def run_scout(queries: list, location: str, hours: int, limit: int, remote_only: bool, sites: list):
    workspace_dir = Path(os.environ.get("WORKSPACE_DIR", Path(__file__).resolve().parent.parent)).resolve()
    applied_data = get_already_applied_titles(workspace_dir)
    
    # Sanitize sites for Finland
    if "finland" in location.lower():
        valid_sites = [s for s in sites if s in ["linkedin", "indeed"]]
        country_indeed = "finland"
    else:
        valid_sites = sites
        country_indeed = "usa"
        
    if not valid_sites:
        valid_sites = ["linkedin", "indeed"]
        
    print(f"\n🚀 Starting Streamlined IT Job Scout at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...")
    print(f"📍 Location: {location} | Remote Only: {remote_only} | Lookback: {hours}h | Platforms: {valid_sites} + Duunitori")
    
    all_jobs = []
    
    for query in queries:
        print(f"\n🔍 Searching for: '{query}' across {', '.join(valid_sites)} & Duunitori...")
        for site in valid_sites:
            try:
                jobs: pd.DataFrame = scrape_jobs(
                    site_name=[site],
                    search_term=query,
                    location=location,
                    results_wanted=limit,
                    hours_old=hours,
                    country_indeed=country_indeed,
                    is_remote=remote_only,
                    linkedin_fetch_description=True
                )
                
                if jobs is not None and not jobs.empty:
                    print(f"   ↳ [{site.capitalize()}] Found {len(jobs)} postings")
                    all_jobs.append(jobs)
                else:
                    print(f"   ↳ [{site.capitalize()}] 0 postings")
            except Exception as e:
                print(f"   ⚠️ [{site.capitalize()}] encountered notice: {e}")

        # Query Duunitori for Finnish local opportunities
        if "finland" in location.lower() or "duunitori" in sites:
            try:
                duuni_list = scrape_duunitori(query, location, limit=limit)
                if duuni_list:
                    print(f"   ↳ [Duunitori] Found {len(duuni_list)} postings")
                    all_jobs.append(pd.DataFrame(duuni_list))
                else:
                    print(f"   ↳ [Duunitori] 0 postings")
            except Exception as e:
                print(f"   ⚠️ [Duunitori] notice: {e}")
            
    if not all_jobs:
        print("\n❌ No jobs found across the specified criteria. Try widening the hours or search terms.")
        return None
        
    combined_df = pd.concat(all_jobs, ignore_index=True)
    raw_count = len(combined_df)
    
    # 1. Convert to dict records for processing
    raw_records = combined_df.to_dict(orient="records")
    
    # 2. Strict IT-Only Filtering
    it_records = []
    for r in raw_records:
        title = str(r.get("title", ""))
        desc = str(r.get("description", ""))
        if is_it_job(title, desc):
            it_records.append(r)
            
    print(f"\n🎯 Filtered for IT-only roles: {len(it_records)} of {raw_count} raw postings (removed {raw_count - len(it_records)} non-IT postings)")
    
    # 3. Language Requirement Classification & Scoring
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
        
        # Check if already tracked/applied
        is_already_tracked = False
        company_clean = company.lower().strip()
        title_clean = title.lower().strip()
        for applied in applied_data:
            if isinstance(applied, str) and ((company_clean in applied and len(company_clean) > 3) or (title_clean in applied and len(title_clean) > 8)):
                is_already_tracked = True
                break
                
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
            "description_snippet": description[:300].replace('\n', ' ') + "..." if description else "N/A"
        })
        
    # 4. Cross-Site Deduplication Engine
    deduped_records = deduplicate_job_records(scored_records)
    print(f"✨ Cross-site deduplication merged multi-platform postings: {len(scored_records)} -> {len(deduped_records)} unique IT roles")
    
    # 5. Sort by match score descending
    deduped_records.sort(key=lambda x: x["match_score"], reverse=True)
    
    # 6. Generate Markdown Report
    output_md = workspace_dir / "JOB_SCOUT_FEED.md"
    
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(f"# Automated IT Job Scout Feed\n\n")
        f.write(f"**Last Scanned:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n")
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
            f.write(f"- **Summary:** {job['description_snippet']}\n")
            f.write(f"- **Instant Workflow Command:** Tell Antigravity:\n")
            f.write(f"  > `do the workflow for this: {job['url']}`\n\n")
            
    # 6. Save Structured JSON Report
    output_json = workspace_dir / "scout_latest_report.json"
    report_data = {
        "last_scanned": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
                "description_snippet": j.get("description_snippet", "")
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
            print(f"📱 Dispatching Telegram alerts for {len(unnotified_new_high)} new high-match IT roles...")
            for j in unnotified_new_high[:3]:
                notify_new_job_opportunity(j)

            # Notify scan summary only when new roles were discovered
            notify_scout_scan_summary(
                new_roles_count=len(unnotified_new_high),
                high_match_count=sum(1 for j in unnotified_new_high if j["match_score"] >= 80),
                total_unique=report_data["deduped_count"]
            )
        else:
            print("📱 Telegram alerts: No new postings to send (all active roles already alerted).")
    except Exception as e:
        print(f"⚠️ Telegram notification notice: {e}")

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
                print(f"🧹 Retention policy cleaned {ret_res['removed_count']} expired posting packages (freed {ret_res['freed_mb']} MB)")
    except Exception as e:
        print(f"⚠️ Retention cleanup notice: {e}")

    print(f"\n✅ Scan complete! Saved streamlined IT feed to: {output_md}")
    return output_md

def main():
    parser = argparse.ArgumentParser(description="Automated IT Job Discovery Scout for Finland & EU Remote")
    parser.add_argument("--queries", "-q", nargs="+", default=[
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
        "IT Trainee"
    ], help="Search terms to query")
    parser.add_argument("--location", "-l", default="Finland", help="Target location (default: Finland)")
    parser.add_argument("--hours", "-t", type=int, default=168, help="Hours old to search (default: 168h / 7 days)")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Results wanted per query per site (default: 10)")
    parser.add_argument("--remote", action="store_true", help="Filter for remote jobs only")
    parser.add_argument("--sites", nargs="+", default=["linkedin", "indeed"], help="Sites to scrape")
    
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
