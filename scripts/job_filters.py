#!/usr/bin/env python3
"""
job_filters.py - Shared IT Role Filtering, Language Detection & Cross-Site Deduplication Engine
"""

import re
from typing import Dict, List, Tuple, Any

# Explicit Non-IT roles to strictly exclude
NON_IT_KEYWORDS = [
    # Mechanical, Civil, Structural, Physical engineering
    "mechanical", "mekaniikka", "civil", "structural", "rakennus", "lvi",
    "nuclear", "ydin", "chemical", "kemian", "metallurgy", "metallurgian",
    "materials science", "thermal", "hvac", "geotechnical",
    # Legal & Compliance (non-IT)
    "litigation", "jurist", "lawyer", "legal", "oikeudellinen", "asianajaja", "paralegal",
    # Corporate Finance, Accounting, Tax
    "corporate finance", "accounting", "kirjanpitäjä", "tilintarkastaja", "tax",
    "payroll", "palkkahallinto", "auditor", "investment banking",
    # Hospitality, Service, Retail non-IT
    "tarjoilija", "kokki", "waiter", "chef", "siivooja", "cleaner", "housekeeper",
    "sairaanhoitaja", "nurse", "lähihoitaja", "physician", "lääkäri", "dentist", "hammaslääkäri",
    "myyjä", "store clerk", "telemarkkinointi", "puhelinmyyjä", "barista",
    "receptionist", "vastaanottovirkailija", "security guard", "vartija",
    # Lab technician (biology / chemistry)
    "lab technician", "laborantti", "biomedical laboratory",
    # Non-IT Sales & Marketing
    "sales development trainee", "sales executive", "real estate", "kiinteistönvälittäjä"
]

# Positive IT domain keywords that must be represented in title or tech stack
IT_TITLE_KEYWORDS = [
    "it", "ict", "software", "ohjelmisto", "developer", "kehittäjä",
    "system", "systems", "järjestelmä", "administrator", "admin", "ylläpitäjä",
    "support", "tuki", "lähituki", "helpdesk", "service desk", "servicedesk",
    "security", "tietoturva", "soc", "siem", "cyber", "kyber",
    "cloud", "pilvi", "azure", "aws", "gcp",
    "network", "verkko", "infrastructure", "infrastruktuuri",
    "devops", "dev ops", "dv ops", "secops", "sec ops", "devsecops", "sysops", "cloud ops", "cloud operations",
    "sre", "site reliability", "data center", "datacenter", "konesali",
    "field service", "field technician", "hardware", "laite", "iot", "scada", "ot",
    "data", "database", "tietokanta", "erp", "crm", "dynamics", "sap",
    "solutions architect", "solution architect", "platform engineer"
]

IT_TECH_STACK_KEYWORDS = [
    "linux", "windows server", "active directory", "entra id", "microsoft 365", "m365",
    "intune", "powershell", "bash", "python", "docker", "kubernetes", "vmware",
    "proxmox", "sql", "cisco", "firewall", "vpn", "tcp/ip", "vlan", "itil",
    "servicenow", "jira", "zendesk", "siem", "splunk", "wazuh", "opc ua", "modbus",
    "git", "api", "rest api", "cloud", "ci/cd", "terraform", "ansible", "gitlab",
    "github actions", "jenkins"
]

# Known Company Aliases for Cross-Site Deduplication
COMPANY_ALIASES = {
    "sogeti": "capgemini",
    "capgemini finland": "capgemini",
    "plandent": "planmeca",
    "plandent oy": "planmeca",
    "santa monica networks": "elisa",
    "elisa oyj": "elisa",
    "varian": "siemens healthineers",
    "siemens healthineers finland": "siemens healthineers",
    "iron systems, inc": "iron systems",
    "iron systems inc": "iron systems",
    "titanium oyj": "titanium",
    "kaukora oy": "kaukora",
    "eezy": "kaukora", # Recruitment partner
    "gapit nordics as": "gapit nordics",
    "nebius b.v.": "nebius"
}

def is_it_job(title: str, description: str = "") -> bool:
    """Strictly evaluates whether a posting is an IT, systems, security, or tech role."""
    t_low = title.lower()
    d_low = (description or "").lower()[:1500]
    
    # 1. Reject if title matches non-IT negative keyword
    for kw in NON_IT_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", t_low):
            return False
            
    # 2. Check if title contains explicit IT keywords
    has_it_title = any(re.search(r"\b" + re.escape(kw) + r"\b", t_low) for kw in IT_TITLE_KEYWORDS)
    if has_it_title:
        # If title is broad/generic (e.g. just "Engineer", "Analyst", "Trainee", "Specialist")
        generic_tokens = {"engineer", "analyst", "trainee", "specialist", "consultant"}
        title_tokens = set(re.findall(r"\w+", t_low))
        if title_tokens.issubset(generic_tokens) or (len(title_tokens) <= 3 and any(t in generic_tokens for t in title_tokens) and not any(k in t_low for k in ["it", "software", "cloud", "security", "data", "support", "network", "system", "cyber", "devops", "secops", "sec ops", "dev ops", "dv ops", "devsecops", "sysops"])):
            # Require at least 2 IT tech stack keywords in description
            tech_hits = sum(1 for kw in IT_TECH_STACK_KEYWORDS if kw in d_low)
            return tech_hits >= 2
        return True
        
    # 3. Check if description has overwhelming IT density even if title is obscure
    tech_hits = sum(1 for kw in IT_TECH_STACK_KEYWORDS if kw in d_low)
    return tech_hits >= 4

def detect_language_requirement(title: str, description: str = "") -> Dict[str, str]:
    """
    Classifies Finnish language requirements from job posting text.
    Returns dict with:
      - 'tag': 'Finnish Required' | 'Finnish Advantage' | 'English / International'
      - 'badge': '🇫🇮 Finnish Required' | '👍 Finnish Advantage' | '🌐 English / Int\'l'
      - 'class': 'badge-warning' | 'badge-info' | 'badge-success'
    """
    combined = f"{title} {description}".lower()
    
    # Explicit Finnish Required patterns
    finnish_req_patterns = [
        r"suomen\s+kielen\s+sujuva",
        r"sujuva\s+suomen\s+kiel",
        r"suomen\s+kieli\s+(?:on\s+)?(?:edellytys|pakollinen|vaaditaan|edellytyksenä)",
        r"edellytämme\s+sujuvaa\s+suomen",
        r"sujuvaa\s+suomea\s+ja\s+englantia",
        r"suomi\s+ja\s+englanti\s+sujuvasti",
        r"fluent\s+finnish",
        r"finnish\s+is\s+(?:mandatory|required|essential)",
        r"fluent\s+in\s+(?:both\s+)?(?:english\s+and\s+finnish|finnish\s+and\s+english)",
        r"good\s+command\s+of\s+finnish\s+and\s+english",
        r"must\s+speak\s+finnish",
        r"proficiency\s+in\s+finnish",
        r"suomen\s+ja\s+englannin\s+kielen\s+sujuva",
        r"vahva\s+suomen\s+ja\s+englannin"
    ]
    
    # Finnish Advantage / Optional patterns
    finnish_adv_patterns = [
        r"suom(?:i|en\s+kielen\s+taito)\s+(?:katsotaan\s+)?eduksi",
        r"finnish\s+is\s+(?:an?\s+)?(?:advantage|plus|asset|bonus|great advantage)",
        r"knowledge\s+of\s+finnish\s+is\s+(?:an?\s+)?(?:advantage|plus|bonus|asset)",
        r"suomen\s+kieli\s+on\s+plussaa",
        r"fluency\s+in\s+finnish\s+is\s+a\s+great\s+advantage",
        r"finnish\s+skills\s+are\s+(?:considered\s+)?an\s+advantage"
    ]
    
    for pat in finnish_adv_patterns:
        if re.search(pat, combined):
            return {
                "tag": "Finnish Advantage",
                "badge": "👍 Finnish Advantage",
                "class": "border-sky-500/30 bg-sky-500/10 text-sky-300"
            }
            
    for pat in finnish_req_patterns:
        if re.search(pat, combined):
            return {
                "tag": "Finnish Required",
                "badge": "🇫🇮 Finnish Required",
                "class": "border-amber-500/30 bg-amber-500/10 text-amber-300"
            }
            
    # Check if mostly Finnish text without explicit requirement
    finnish_markers = ["tehtävässä", "etsimme", "tarjoamme", "edellytämme", "hakijalta", "työskentelet", "kokemusta", "osaamista"]
    fi_word_count = sum(1 for w in finnish_markers if w in combined)
    if fi_word_count >= 4:
        return {
            "tag": "Finnish Required",
            "badge": "🇫🇮 Finnish Required",
            "class": "border-amber-500/30 bg-amber-500/10 text-amber-300"
        }
        
    return {
        "tag": "English / International",
        "badge": "🌐 English / Int'l",
        "class": "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
    }

def normalize_company(company: str) -> str:
    """Canonical normalization for company names to merge cross-site postings."""
    c = (company or "").lower().strip()
    for alias, canonical in COMPANY_ALIASES.items():
        if alias in c:
            return canonical
            
    # Strip corporate abbreviations
    c = re.sub(r"\b(oy|oyj|ab|inc|ltd|gmbh|technologies|technology|group|finland|solutions|consulting|nordics|services)\b", "", c)
    c = re.sub(r"[^a-z0-9]", "", c)
    return c

def normalize_title(title: str) -> str:
    """Canonical normalization for job titles to match across platforms."""
    t = (title or "").lower().strip()
    t = re.sub(r"\([^)]*\)", "", t) # remove parentheticals like (m/f/d), (ERP), (German speaking)
    t = re.sub(r"[-–—/|].*$", "", t) # remove trailing qualifiers like "- Finland" or "/ Tampere"

    # Common English spelling variants (British vs American)
    t = re.sub(r"\bdefen[cs]e\b", "defense", t)
    t = re.sub(r"\bcent(?:re|er)\b", "center", t)
    t = re.sub(r"\banaly[sz]e[rs]?\b", "analyzer", t)
    t = re.sub(r"\borgani[sz]ation\b", "organization", t)
    t = re.sub(r"\bspeciali[sz]ed\b", "specialized", t)

    # Common tech compounding and abbreviation variants
    t = re.sub(r"\b(?:dev\s*ops|dv\s*ops)\b", "devops", t)
    t = re.sub(r"\bsec\s*ops\b", "secops", t)
    t = re.sub(r"\bdev\s*sec\s*ops\b", "devsecops", t)
    t = re.sub(r"\bsys\s*ops\b", "sysops", t)
    t = re.sub(r"\bcyber\s*security\b", "cybersecurity", t)
    t = re.sub(r"\bfull\s*stack\b", "fullstack", t)
    t = re.sub(r"\bfront\s*end\b", "frontend", t)
    t = re.sub(r"\bback\s*end\b", "backend", t)
    t = re.sub(r"\bcloud\s*ops\b", "cloudops", t)

    # Remove location suffixes
    t = re.sub(r"\b(finland|helsinki|espoo|tampere|turku|remote|hybrid|onsite|emea)\b", "", t)
    # Remove level prefixes to group identical functional roles
    t = re.sub(r"\b(junior|entry\s+level|trainee|senior|lead|associate|principal)\b", "", t)
    t = re.sub(r"[^a-z0-9]", "", t)
    return t

def deduplicate_job_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Groups and merges duplicate job postings appearing across multiple job sites
    (e.g., LinkedIn and Indeed), keeping rich multi-platform links and metadata.
    """
    grouped = {}
    
    for r in records:
        comp_raw = r.get("company", "Unknown")
        tit_raw = r.get("title", "Unknown")
        
        comp_norm = normalize_company(comp_raw)
        tit_norm = normalize_title(tit_raw)
        
        # Fallback key if company or title is empty
        if not comp_norm:
            key = f"url_{r.get('job_url', r.get('url', ''))}"
        else:
            key = f"{comp_norm}_{tit_norm}"
            
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(r)
        
    deduped = []
    for key, items in grouped.items():
        if len(items) == 1:
            item = items[0]
            # Ensure platforms list
            site = str(item.get("site", item.get("platform", "Web"))).capitalize()
            item["platforms"] = [site]
            item["platform_display"] = site
            deduped.append(item)
        else:
            # Merge duplicate postings across sites
            primary = None
            # Prefer LinkedIn or the record with the longest description
            for item in items:
                site = str(item.get("site", item.get("platform", ""))).lower()
                if "linkedin" in site:
                    primary = item.copy()
                    break
            if not primary:
                primary = max(items, key=lambda x: len(str(x.get("description", "")))).copy()
                
            all_sites = set()
            all_urls = []
            for item in items:
                s = str(item.get("site", item.get("platform", "Web"))).capitalize()
                all_sites.add(s)
                u = item.get("job_url") or item.get("url")
                if u and u not in all_urls:
                    all_urls.append(u)
                    
            primary["platforms"] = sorted(list(all_sites))
            primary["platform_display"] = " + ".join(sorted(list(all_sites)))
            primary["all_urls"] = all_urls
            # Take highest match score
            primary["match_score"] = max(item.get("match_score", 0) for item in items)
            primary["already_applied"] = any(item.get("already_applied", False) for item in items)
            deduped.append(primary)
            
    return deduped

def get_candidate_contact_info(workspace_dir=None) -> dict:
    """
    Dynamically loads candidate personal info from Base_CV.md or environment variables.
    Keeps source code generic and prevents hardcoding PII.
    """
    import os
    from pathlib import Path
    
    ws = Path(workspace_dir) if workspace_dir else Path(os.environ.get("WORKSPACE_DIR", Path(__file__).resolve().parent.parent))
    
    info = {
        "name": os.environ.get("CANDIDATE_NAME", "Candidate Name"),
        "location": os.environ.get("CANDIDATE_LOCATION", "Helsinki / Turku, Finland (Full EU Work Authorization / Resident)"),
        "phone": os.environ.get("CANDIDATE_PHONE", "+358 00 0000000"),
        "email": os.environ.get("CANDIDATE_EMAIL", "candidate@example.com"),
        "linkedin": os.environ.get("CANDIDATE_LINKEDIN", "https://linkedin.com"),
        "github": os.environ.get("CANDIDATE_GITHUB", "https://github.com"),
        "languages": "English (Fluent/C1), Finnish (Conversational / Actively studying)"
    }
    
    base_cv = ws / "Base_CV.md"
    if not base_cv.exists():
        base_cv = Path(__file__).resolve().parent.parent / "Base_CV.md"
        
    if base_cv.exists():
        try:
            content = base_cv.read_text(encoding="utf-8", errors="ignore")
            in_header = True
            for line in content.splitlines():
                line_str = line.strip()
                if line_str.startswith("## "):
                    in_header = False
                    break
                if line_str.startswith("# ") and info["name"] in ("Candidate Name", ""):
                    raw_name = line_str[2:].split(":")[0].strip()
                    if raw_name:
                        info["name"] = raw_name
                elif "**Phone:**" in line_str:
                    m = re.search(r'\+?[0-9][0-9\s\-]+', line_str)
                    if m:
                        info["phone"] = m.group(0).strip()
                elif "**Email:**" in line_str:
                    m = re.search(r'[\w\.-]+@[\w\.-]+', line_str)
                    if m:
                        info["email"] = m.group(0).strip()
                elif "**LinkedIn:**" in line_str:
                    m = re.search(r'https?://[^\s\)]+', line_str)
                    if m:
                        info["linkedin"] = m.group(0).strip()
                elif "**GitHub:**" in line_str:
                    m = re.search(r'https?://[^\s\)]+', line_str)
                    if m:
                        info["github"] = m.group(0).strip()
                elif "**Location:**" in line_str:
                    raw_loc = line_str.replace("**Location:**", "").strip()
                    if raw_loc:
                        info["location"] = raw_loc
        except Exception:
            pass
            
    return info
