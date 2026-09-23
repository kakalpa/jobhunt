#!/usr/bin/env python3
"""
contact_extractor.py - High-Precision Contact & Recruiter Discovery Engine
Extracts potential contact persons, hiring managers, direct emails, phone numbers,
and calling hours from job postings and descriptions (Finnish & English).
"""

import re
import urllib.parse
from typing import Dict, List, Any, Optional

# Generic organizational prefixes to avoid treating as person names
GENERIC_EMAIL_PREFIXES = {
    "info", "rekry", "rekrytointi", "jobs", "careers", "contact", "contacts",
    "hr", "support", "sales", "admin", "recruitment", "talent", "team",
    "apply", "application", "helpdesk", "office", "asiakaspalvelu", "viestinta"
}

IGNORED_EMAIL_DOMAINS = {
    "example.com", "w3.org", "schema.org", "noreply", "no-reply", "dontreply",
    "sentry.io", "github.com", "google.com", "apple.com"
}

# Known Finnish and English organizational titles to identify roles and names
TITLE_INDICATORS = [
    r"head of\s+[\w\s&]+",
    r"director of\s+[\w\s&]+",
    r"vp of\s+[\w\s&]+",
    r"recruiting manager",
    r"recruitment manager",
    r"talent acquisition partner",
    r"talent partner",
    r"recruiter",
    r"hiring manager",
    r"engineering manager",
    r"it manager",
    r"it-päällikkö",
    r"it päällikkö",
    r"rekrytointipäällikkö",
    r"rekrytoija",
    r"tiiminvetäjä",
    r"team lead",
    r"operations manager",
    r"project manager",
    r"service manager",
    r"technical lead",
    r"cto",
    r"cio",
    r"ciso",
    r"toimitusjohtaja",
    r"henkilöstöpäällikkö",
    r"hr manager",
    r"hr partner",
    r"hr-asiantuntija",
    r"asiantuntija"
]

def name_from_email(email: str) -> str:
    """Derives a capitalized human name from firstname.lastname@domain emails."""
    if not email or "@" not in email:
        return ""
    user = email.split("@")[0].strip()
    if user.lower() in GENERIC_EMAIL_PREFIXES:
        return ""
    parts = re.split(r"[\.\-_]", user)
    if len(parts) >= 2 and all(p.isalpha() and len(p) >= 2 for p in parts):
        return " ".join(p.capitalize() for p in parts)
    return ""

def extract_job_contacts(
    description: str,
    title: str = "",
    company: str = "",
    candidate_emails: Optional[set] = None
) -> Dict[str, Any]:
    """
    Parses a job description or scraped posting to extract contact persons,
    hiring managers, recruiters, direct emails, phone numbers, and calling hours.
    """
    result: Dict[str, Any] = {
        "has_contacts": False,
        "primary_name": "",
        "primary_title": "",
        "primary_email": "",
        "primary_phone": "",
        "calling_hours": "",
        "emails": [],
        "phones": [],
        "contacts": [],
        "contact_snippets": [],
        "linkedin_search_url": "",
        "mailto_url": ""
    }

    if not description:
        comp_clean = company.strip() if company and company != "Company" else ""
        if comp_clean:
            q = f"{comp_clean} recruiter OR \"hiring manager\""
            result["linkedin_search_url"] = f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote_plus(q)}"
        return result

    if candidate_emails is None:
        candidate_emails = {"kalpakuruwita@gmail.com", "candidate@example.com"}

    text = description.replace("\r", "")

    # 1. Extract Emails
    raw_emails = re.findall(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", text)
    valid_emails: List[str] = []
    for em in raw_emails:
        em_clean = em.strip().rstrip(".,;:-")
        em_low = em_clean.lower()
        if em_low in candidate_emails:
            continue
        if any(ign in em_low for ign in IGNORED_EMAIL_DOMAINS):
            continue
        if em_clean not in valid_emails:
            valid_emails.append(em_clean)

    # 2. Extract Phone Numbers (Finnish + international formats)
    phone_pattern = r"(?:(?:\+358|00358)\s*(?:\(0\))?\s*[1-9]\d{0,2}[0-9\s\-]{4,11}|(?:\b0[1-9]\d{0,2}[0-9\s\-]{4,10}))"
    raw_phones = re.findall(phone_pattern, text)
    valid_phones: List[str] = []
    for ph in raw_phones:
        digits = re.sub(r"[^\d]", "", ph)
        if 7 <= len(digits) <= 13:
            # Avoid matching years e.g. 2024-2026 or salary ranges
            if not ph.startswith("+") and digits.startswith(("202", "199", "198")):
                continue
            cleaned = re.sub(r"\s+", " ", ph.strip().rstrip(".,;:-"))
            if cleaned not in valid_phones:
                valid_phones.append(cleaned)

    # 3. Extract Specific Contact Inquiry Snippets
    contact_keywords_re = (
        r"(?:lisätieto(?:ja|ja tehtävästä|ja hausta|ja roolista|ja paikasta)?|"
        r"yhteystiedot|kysymyksiin vastaa|yhteyshenkilö|rekrytoinnista vastaa|soitathan|"
        r"ota yhteyttä|haluatko tietää lisää\??|"
        r"for more information|further information|questions regarding|contact person|"
        r"hiring manager|point of contact|feel free to reach out to|please contact|"
        r"for inquiries|for further inquiries|direct inquiries to)"
    )

    snippets: List[str] = []
    for line in text.splitlines():
        line_clean = line.strip()
        if not line_clean or len(line_clean) < 10:
            continue
        if re.search(contact_keywords_re, line_clean, re.I):
            cleaned_snip = re.sub(r"[\*_#`\[\]]", "", line_clean)
            cleaned_snip = re.sub(r"\s+", " ", cleaned_snip).strip()
            if cleaned_snip not in snippets:
                snippets.append(cleaned_snip)

    # 4. Extract Calling Hours
    calling_hours = ""
    hours_patterns = [
        r"(?:soittoajat?|tavoitat parhaiten|parhaiten tavoitettavissa|available for calls|call hours|reach me at|available)[:\s]+([^\.\n;]+(?:\d{1,2}[\.\:\-]\d{2}|\d{1,2}\s*-\s*\d{1,2})[^\.\n;]*)",
        r"(?:tiistaisin|keskiviikkoisin|torstaisin|perjantaisin|maanantaisin|arkisin)[^\.\n;]*(?:klo|kello)\s*\d{1,2}(?:[\.\:\-]\d{2})?(?:\s*-\s*\d{1,2}(?:[\.\:\-]\d{2})?)?"
    ]
    for pat in hours_patterns:
        m_h = re.search(pat, text, re.I)
        if m_h:
            calling_hours = re.sub(r"[\*_#]", "", m_h.group(0)).strip()
            break

    # 5. Extract Contact Persons
    parsed_contacts: List[Dict[str, str]] = []
    seen_names = set()

    for snip in snippets:
        snip_email = ""
        em_m = re.search(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", snip)
        if em_m:
            snip_email = em_m.group(0).rstrip(".,;:-")

        snip_phone = ""
        ph_m = re.search(phone_pattern, snip)
        if ph_m:
            snip_phone = re.sub(r"\s+", " ", ph_m.group(0)).strip().rstrip(".,;:-")

        person_name = ""
        person_title = ""

        if snip_email:
            derived = name_from_email(snip_email)
            if derived:
                person_name = derived

        for t_pat in TITLE_INDICATORS:
            t_match = re.search(rf"\b({t_pat})\b", snip, re.I)
            if t_match:
                person_title = t_match.group(1).title()
                break

        if not person_name:
            s_stripped = re.sub(contact_keywords_re, "", snip, flags=re.I)
            candidates = re.findall(r"\b[A-ZÄÖÅ][a-zäöå]+(?:\-[A-ZÄÖÅ][a-zäöå]+)?\s+[A-ZÄÖÅ][a-zäöå]+(?:\s+[A-ZÄÖÅ][a-zäöå]+)?\b", s_stripped)
            for cand in candidates:
                cand_low = cand.lower()
                if any(bad in cand_low for bad in [
                    "information about", "further information", "recruiting manager",
                    "technical sales", "senior talent", "working hours", "human resources",
                    "equal opportunity", "service desk", "data center", "tuas", "finland"
                ]):
                    continue
                person_name = cand
                break

        if person_name or snip_email or snip_phone:
            n_key = person_name.lower() if person_name else (snip_email.lower() if snip_email else snip_phone)
            if n_key and n_key not in seen_names:
                seen_names.add(n_key)
                parsed_contacts.append({
                    "name": person_name,
                    "title": person_title,
                    "email": snip_email,
                    "phone": snip_phone,
                    "snippet": snip
                })

    for em in valid_emails:
        derived_name = name_from_email(em)
        if derived_name and derived_name.lower() not in seen_names:
            seen_names.add(derived_name.lower())
            parsed_contacts.append({
                "name": derived_name,
                "title": "",
                "email": em,
                "phone": "",
                "snippet": f"Contact email: {em}"
            })

    primary_name = ""
    primary_title = ""
    primary_email = ""
    primary_phone = ""

    if parsed_contacts:
        best = max(parsed_contacts, key=lambda c: (bool(c["name"]), bool(c["email"]), bool(c["phone"])))
        primary_name = best["name"]
        primary_title = best["title"]
        primary_email = best["email"]
        primary_phone = best["phone"]

    if not primary_email and valid_emails:
        primary_email = valid_emails[0]
    if not primary_phone and valid_phones:
        primary_phone = valid_phones[0]

    comp_clean = company.strip() if company and company != "Company" else ""
    if comp_clean:
        if primary_name:
            search_query = f"{comp_clean} {primary_name}"
        elif primary_title:
            search_query = f"{comp_clean} {primary_title}"
        elif title:
            search_query = f"{comp_clean} recruiter OR \"hiring manager\""
        else:
            search_query = f"{comp_clean} recruiter"
        result["linkedin_search_url"] = f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote_plus(search_query)}"

    if primary_email:
        subj = f"Regarding {title} Role" if title else "Job Application Inquiry"
        result["mailto_url"] = f"mailto:{primary_email}?subject={urllib.parse.quote(subj)}"

    result["has_contacts"] = bool(primary_name or primary_email or primary_phone or parsed_contacts)
    result["primary_name"] = primary_name
    result["primary_title"] = primary_title
    result["primary_email"] = primary_email
    result["primary_phone"] = primary_phone
    result["calling_hours"] = calling_hours
    result["emails"] = valid_emails
    result["phones"] = valid_phones
    result["contacts"] = parsed_contacts
    result["contact_snippets"] = snippets[:4]

    return result

def format_contacts_markdown(contacts_info: Dict[str, Any], company: str = "", title: str = "") -> str:
    """Formats detected contact info as clean markdown for job descriptions and scout reports."""
    if not contacts_info or not contacts_info.get("has_contacts"):
        comp_str = company if company and company != "Company" else "the company"
        li_url = contacts_info.get("linkedin_search_url", "")
        li_link = f"[Search {comp_str} Recruiters on LinkedIn]({li_url})" if li_url else "Search LinkedIn"
        return f"*(No direct contact person was listed in the posting text. {li_link})*\n"

    lines = []
    if contacts_info.get("primary_name"):
        name_str = f"**{contacts_info['primary_name']}**"
        if contacts_info.get("primary_title"):
            name_str += f" ({contacts_info['primary_title']})"
        lines.append(f"- **Contact Person:** {name_str}")
    elif contacts_info.get("primary_title"):
        lines.append(f"- **Role:** {contacts_info['primary_title']}")

    if contacts_info.get("primary_email"):
        mail_link = contacts_info.get("mailto_url") or f"mailto:{contacts_info['primary_email']}"
        lines.append(f"- **Email:** [{contacts_info['primary_email']}]({mail_link})")

    if contacts_info.get("primary_phone"):
        lines.append(f"- **Phone:** `{contacts_info['primary_phone']}`")

    if contacts_info.get("calling_hours"):
        lines.append(f"- **Calling Hours:** *{contacts_info['calling_hours']}*")

    if contacts_info.get("linkedin_search_url"):
        lines.append(f"- **LinkedIn Directory:** [Find on LinkedIn]({contacts_info['linkedin_search_url']})")

    if contacts_info.get("contact_snippets"):
        snip = contacts_info["contact_snippets"][0]
        lines.append(f"- **Posting Note:** > *\"{snip}\"*")

    return "\n".join(lines) + "\n"
