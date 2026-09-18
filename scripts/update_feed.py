#!/usr/bin/env python3
"""
update_feed.py - Re-indexes scouted CSV data, filters for IT roles only,
detects Finnish language requirements, and eliminates cross-site duplicates.
"""

import sys
import json
import pandas as pd
from datetime import datetime
from pathlib import Path

# Add project root and scripts dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.job_filters import is_it_job, detect_language_requirement, deduplicate_job_records, get_candidate_contact_info

workspace = Path(__file__).resolve().parent.parent
tracker_content = (workspace / "APPLICATIONS_TRACKER.md").read_text(encoding="utf-8", errors="ignore").lower()

csv_file = workspace / "entry_level_scouted.csv"
if not csv_file.exists():
    print("❌ entry_level_scouted.csv not found.")
    sys.exit(1)

df = pd.read_csv(csv_file)
raw_count = len(df)

# 1. Filter for IT-only jobs
raw_records = df.to_dict(orient="records")
it_records = []
for r in raw_records:
    title = str(r.get("title", ""))
    desc = str(r.get("description", ""))
    if is_it_job(title, desc):
        it_records.append(r)

print(f"Filtered: {raw_count} raw -> {len(it_records)} IT-only roles (removed {raw_count - len(it_records)} non-IT rows)")

# 2. Score and classify language
scored_records = []
for r in it_records:
    title = str(r.get("title", "Unknown Title"))
    company = str(r.get("company", "Unknown Company"))
    location = str(r.get("location", "Finland"))
    url = str(r.get("job_url", ""))
    site = str(r.get("site", "Web"))
    desc = str(r.get("description", ""))
    
    lang_info = detect_language_requirement(title, desc)
    
    # Calculate score
    score = 70
    t_low = title.lower()
    c_low = f"{t_low} {desc.lower()}"
    if any(k in t_low for k in ["junior", "entry level", "graduate", "trainee", "service desk", "helpdesk", "support specialist"]):
        score += 10
    if any(k in c_low for k in ["security", "soc", "siem", "splunk", "wazuh", "active directory", "azure", "linux", "hardware", "cabling", "dl380", "opc ua"]):
        score += 8
    if any(k in c_low for k in ["python", "bash", "powershell", "troubleshooting", "break-fix", "itil"]):
        score += 5
    score = min(score, 96)
    
    comp_clean = company.lower().strip()
    already_applied = (comp_clean in tracker_content and len(comp_clean) > 3)
    
    scored_records.append({
        "title": title,
        "company": company,
        "location": location,
        "site": site,
        "job_url": url,
        "url": url,
        "match_score": score,
        "already_applied": already_applied,
        "language_tag": lang_info["tag"],
        "language_badge": lang_info["badge"],
        "description": desc,
        "description_snippet": desc[:250].replace('\n', ' ') + "..." if desc else "N/A"
    })

# 3. Cross-Site Deduplication
deduped_records = deduplicate_job_records(scored_records)
print(f"Cross-site deduplicated: {len(scored_records)} -> {len(deduped_records)} unique IT openings")

# 4. Sort by match score
deduped_records.sort(key=lambda x: x["match_score"], reverse=True)

# 5. Write to JOB_SCOUT_FEED.md
feed_path = workspace / "JOB_SCOUT_FEED.md"
now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

with open(feed_path, "w", encoding="utf-8") as f:
    f.write("# Automated IT Job Scout Feed\n\n")
    f.write(f"**Last Scanned:** {now_str}  \n")
    f.write("**Focus:** Curated IT, Systems, Cloud, Security & Support Positions in Finland  \n")
    f.write(f"**Discovered Postings:** {len(deduped_records)} unique openings (Non-IT filtered out, Cross-Site Deduplicated)  \n\n")
    f.write("---\n\n")
    cand = get_candidate_contact_info(workspace)
    f.write(f"## 🎯 Prioritized IT Opportunities for {cand['name']}\n\n")
    f.write("| Match Score | Role Title | Company | Location | Language Requirement | Platform | Status | Direct Link |\n")
    f.write("| :---: | :--- | :--- | :--- | :---: | :---: | :---: | :--- |\n")
    
    for r in deduped_records:
        title = r["title"]
        company = r["company"]
        location = r["location"]
        platform = r.get("platform_display", str(r.get("site", "Web")).capitalize())
        url = r["url"]
        score = r["match_score"]
        lang_badge = r["language_badge"]
        status_tag = "⚠️ Already Tracked" if r["already_applied"] else "⭐ **NEW**"
        
        f.write(f"| **{score}%** | {title} | **{company}** | {location} | {lang_badge} | {platform} | {status_tag} | [Apply / View]({url}) |\n")
        
    f.write("\n---\n\n")
    f.write("## 📋 Top IT Recommendations\n\n")
    for idx, r in enumerate(deduped_records[:8], 1):
        platform = r.get("platform_display", str(r.get("site", "Web")).capitalize())
        f.write(f"### {idx}. {r['title']} — {r['company']}\n")
        f.write(f"- **Estimated Match:** {r['match_score']}% | **Location:** {r['location']} | **Platform:** {platform}\n")
        f.write(f"- **Language Requirement:** {r['language_badge']}\n")
        f.write(f"- **Direct Link:** {r['url']}\n")
        f.write(f"- **Summary:** {r['description_snippet']}\n\n")

    # 6. Save Structured JSON Report
    output_json = workspace / "scout_latest_report.json"
    report_data = {
        "last_scanned": now_str,
        "location": "Finland",
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

# Desktop notification for new high-match roles
new_high = [j for j in deduped_records if j["match_score"] >= 85 and not j["already_applied"]]
if new_high:
    top_role = new_high[0]
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

print(f"✅ Successfully updated JOB_SCOUT_FEED.md with {len(deduped_records)} verified unique IT openings!")
