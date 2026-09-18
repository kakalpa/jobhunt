#!/usr/bin/env python3
"""
ai_tailor.py - Multi-Skill AI Application Tailoring Engine.
Incorporates 12 specialized agent skill modules into Google Gemini generative calls:
  1. finnish-job-market-tailor (Factual precision, language transparency, Supo clearance, Lisätietoja call script)
  2. cover-letter-generator (3-pillar framework, hook, core technical match, length constraint < 750 body chars)
  3. tech-resume-optimizer & resume-bullet-writer & resume-quantifier (Google X-Y-Z formula: Accomplished [X] as measured by [Y] by doing [Z])
  4. application-form-filler (Direct, non-crammed Greenhouse/Lever form answers)
  5. salary-negotiation-prep (Finnish market salary benchmarks & collective agreement context)
  6. cold-email-writer & linkedin-profile-optimizer (LinkedIn connection note < 300 chars, Recruiter InMail, 7-day follow-up)
  7. interview-prep-generator (STAR stories tailored to employer's business, intelligent reverse questions)
  8. resume-ats-optimizer & job-description-analyzer (Exact hard-keyword extraction)
"""

import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", Path(__file__).resolve().parent.parent)).resolve()
sys.path.insert(0, str(WORKSPACE_DIR))
sys.path.insert(0, str(WORKSPACE_DIR / "scripts"))

try:
    from scripts.job_filters import get_candidate_contact_info
except ImportError:
    from job_filters import get_candidate_contact_info

def get_api_key() -> str:
    """Load Gemini API Key from environment or .env file."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key.strip()
    env_path = WORKSPACE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

MODELS = [
    "models/gemini-flash-lite-latest",
    "models/gemini-3.6-flash",
    "models/gemini-2.5-flash"
]

def tailor_application(title: str, company: str, location: str, jd_text: str) -> dict:
    """
    Invokes Gemini to analyze the job posting and tailor the candidate's application
    using all specialized job search skills.
    Returns a dictionary of tailored sections or None on error.
    """
    api_key = get_api_key()
    if not api_key:
        return None

    cleaned_jd = (jd_text or "")[:4500].strip()
    if len(cleaned_jd) < 100:
        return None

    cand = get_candidate_contact_info(WORKSPACE_DIR)
    cand_name = cand["name"]

    prompt = f"""You are an expert AI Career Coach and Application Tailoring Engine for {cand_name}, an experienced IT systems and infrastructure engineer based in Finland.

You are preparing an end-to-end application package for:
COMPANY: {company}
POSITION: {title}
LOCATION: {location}
JOB DESCRIPTION & REQUIREMENTS:
{cleaned_jd}

---
CANDIDATE BASE PROFILE ({cand_name}):
- Academic: B.Eng. in Information Technology, Turku University of Applied Sciences (TUAS), GPA 4.0 / 4.0 (May 2026).
- Experience: 8+ years enterprise systems administration, Linux (RHEL/Ubuntu), Docker containerization, Ansible, Azure cloud, M365, Entra ID, Intune, HPE ProLiant DL20/DL380 bare-metal hardware, and automation (PowerShell, Bash, Python).
- Certifications: TryHackMe SOC Level 1 & PenTest+; Google Cybersecurity Professional; Azure Administrator; Red Hat System Administration (RH124/RH134); ISO/IEC 27005 Information Security Risk Management.
- Achievements: 1st Place Team in 2026 DNCS Live-Fire Cybersecurity Hackathon; sustained 99.9% service uptime; 94% First-Time-Fix rate across 200+ multi-OS workstations.
- Status: Permanent EU Work Authorization / Finland Resident. Notice period: 0 days (Immediate). English: Fluent (C1), Finnish: Conversational / Actively studying.

---
INCORPORATE THESE SPECIALIZED SKILL METHODOLOGIES:
1. finnish-job-market-tailor:
   - Understated, factual tone. Strictly avoid US-style hyperbole ("rockstar", "visionary", "testament").
   - Honest Finnish language transparency (fluent English C1, conversational Finnish actively developing).
   - Emphasize Finnish degree (TUAS B.Eng. 4.0 GPA), local reference (Mr. Tero Virtanen, Senior Lecturer at TUAS), and readiness for Supo standard security clearance (perusmuotoinen turvallisuusselvitys) and drug screening.
   - Generate a 3-minute recruiter call script ("Lisätietoja antaa") with 2 intelligent technical questions.

2. cover-letter-generator:
   - 3-pillar structure: Hook (specific to {company}'s tech/mission), Core technical match with quantifiable results, and Synergy/Availability.
   - Core body must be strictly under 750 characters total, ensuring complete letter stays under 1,500 characters. No salutations or sign-offs.

3. tech-resume-optimizer & resume-bullet-writer & resume-quantifier:
   - Mandatory Google X-Y-Z formula: "Accomplished [X] as measured by [Y], by doing [Z]".
   - Provide 2 bespoke accomplishment bullets spotlighting {company}'s highest-priority technical requirements.

4. application-form-filler:
   - Direct, copy-paste ready form answers for ATS portals (Greenhouse, Lever, Workday): Why this company, technical breakdown, greatest technical strength.

5. salary-negotiation-prep:
   - Realistic Finnish gross monthly salary based on seniority and market rate (e.g. €3,200–€3,800 Support; €4,200–€4,800 Security; €4,500–€5,200 DevOps/Senior Systems; reference collective agreement TES/AVAINTA where relevant).

6. cold-email-writer & linkedin-profile-optimizer:
   - LinkedIn connection note strictly under 300 characters.
   - Recruiter InMail/direct email (human, direct, highlighting 0-day notice).
   - 7-day polite follow-up message.

7. interview-prep-generator:
   - 2 tailored STAR stories (Situation, Task, Action, Result) matching the company's domain.
   - 3 intelligent reverse questions to ask the interviewer.

8. resume-ats-optimizer & job-description-analyzer:
   - 8-10 high-value hard technical keywords extracted directly from the posting.

---
TASK:
Return a STRICT JSON object with these exact keys:
{{
  "cv_summary": "2-3 sentence impactful professional summary highlighting direct overlap with {company}'s priorities.",
  "cv_custom_bullets": [
    "Accomplished [X] as measured by [Y], by doing [Z] bullet 1 targeting {company}'s stack...",
    "Accomplished [X] as measured by [Y], by doing [Z] bullet 2 targeting {company}'s stack..."
  ],
  "cover_letter_body": "1-2 focused body paragraphs (strictly under 750 characters total) explaining technical alignment and contribution to {company}'s mission. Do NOT include salutations or sign-offs.",
  "qa_why": "Direct, genuine 2-3 sentence answer explaining motivation for {company} and this role.",
  "qa_tech": "Direct summary of hands-on experience with the core technologies in the posting (format: [Tech] — [Years]. [Project/Impact]).",
  "qa_strength": "Candidate's greatest technical strength relevant to this role in 2 sentences.",
  "phone_call_script": {{
    "intro": "Brief 1-sentence opening when calling the contact person for Lisätietoja.",
    "question_1": "First intelligent technical question about their infrastructure.",
    "question_2": "Second intelligent question about their deployment automation or roadmap."
  }},
  "linkedin_connect": "Direct connection note strictly under 300 characters.",
  "recruiter_inmail": "Professional, concise outreach note emphasizing 0-day notice and specific alignment.",
  "follow_up": "Courteous 7-day application follow-up message.",
  "interview_star_1": {{
    "title": "Story title",
    "situation": "...",
    "task": "...",
    "action": "...",
    "result": "..."
  }},
  "interview_star_2": {{
    "title": "Story title",
    "situation": "...",
    "task": "...",
    "action": "...",
    "result": "..."
  }},
  "interview_questions": [
    "Question 1",
    "Question 2",
    "Question 3"
  ],
  "salary_guidance": "e.g. €4,800 – €5,200 / month (aligned with Finnish market standards)",
  "ats_keywords": [
    "Keyword1",
    "Keyword2",
    "Keyword3",
    "Keyword4",
    "Keyword5",
    "Keyword6",
    "Keyword7",
    "Keyword8"
  ]
}}
"""

    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2
        }
    }
    payload = json.dumps(data).encode("utf-8")

    for model_name in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                if parsed.get("cv_summary") and parsed.get("cover_letter_body"):
                    print(f"✨ [AI Tailor] Successfully tailored application using {model_name} with all 12 skills!")
                    return parsed
        except Exception as e:
            print(f"⚠️ [AI Tailor] Notice on {model_name}: {e}")
            continue

    return None

def generate_ai_interview_prep(title: str, company: str, location: str, jd_text: str) -> dict:
    """
    Invokes Gemini to generate comprehensive Interview Preparation & Systematic CV Walk-Through
    incorporating interview-prep-generator, finnish-job-market-tailor, and salary-negotiation-prep.
    """
    api_key = get_api_key()
    if not api_key:
        return None

    cleaned_jd = (jd_text or "")[:4500].strip()

    cand = get_candidate_contact_info(WORKSPACE_DIR)
    cand_name = cand["name"]

    prompt = f"""You are an expert Executive Interview Coach and Technical Assessment Specialist for {cand_name}, an experienced IT systems and infrastructure engineer based in Finland.

COMPANY: {company}
POSITION: {title}
LOCATION: {location}
JOB DESCRIPTION / TECHNICAL CONTEXT:
{cleaned_jd}

---
CANDIDATE PROFILE ({cand_name}):
- Education: Bachelor of Engineering in IT, Turku University of Applied Sciences (TUAS), 4.0 / 4.0 GPA (Graduating May 2026). Specialized in OT/IT convergence, telemetry, cybersecurity.
- Enterprise Experience: 8+ years enterprise systems, bare-metal server infrastructure (HPE ProLiant DL20/DL380), Linux (RHEL/Ubuntu), Docker, Ansible, M365, Entra ID, Intune, automation (Python, Bash, PowerShell).
- Track Record: Associate Tech Lead at Mainframe (8+ yrs, 200+ endpoints, 94% First-Time-Fix rate, CAB change management). 1st Place Team in 2026 DNCS Live-Fire Cyber Hackathon.
- Finnish Market Grounding: Permanent EU Work Authorization, 0-day notice (Immediate), valid Finnish driving license, prepared for Supo standard security clearance (perusmuotoinen turvallisuusselvitys) and drug test. Local Reference: Mr. Tero Virtanen (Senior Lecturer, TUAS).

---
TASK:
Generate a comprehensive, tailored Interview Preparation Guide in strict JSON format with these exact keys:
{{
  "elevator_pitch": "1-minute spoken introduction connecting {cand_name}'s background directly to {company} and this role. Understated, confident, highlighting TUAS 4.0 GPA and enterprise experience.",
  "cv_walkthrough": {{
    "tuas_focus": "Systematic narrative bullet points for TUAS B.Eng. research, telemetry/infrastructure projects, hackathon win, and local reference.",
    "mainframe_focus": "Systematic narrative bullet points for 8+ years at Mainframe as Associate Tech Lead, server maintenance, SLA discipline, CAB, and automation.",
    "sumathi_focus": "Systematic narrative bullet points for earlier IT support coordinator role, standardized OS images, and ticketing resolution."
  }},
  "motivation_points": [
    "Reason 1 specific to {company}'s technology, scale, or mission",
    "Reason 2 highlighting technical stack alignment",
    "Reason 3 on operational reliability or engineering culture",
    "Reason 4 on long-term contribution in Finland"
  ],
  "star_scenario_1": {{
    "title": "Scenario title (e.g. Critical Server Fault or Telemetry Drop)",
    "situation": "Realistic technical challenge relevant to {company}'s tech stack",
    "task": "What the candidate had to accomplish within SLA",
    "action": "Concrete technical steps taken (tools, diagnostics, escalation/isolation, commands)",
    "result": "Quantifiable outcome (uptime restored, zero data loss, SLA met)"
  }},
  "star_scenario_2": {{
    "title": "Scenario title (e.g. Infrastructure Automation or Protocol Troubleshooting)",
    "situation": "Second realistic technical challenge relevant to this role",
    "task": "What had to be resolved",
    "action": "Technical actions taken by the candidate",
    "result": "Quantifiable outcome"
  }},
  "reverse_questions": [
    "High-IQ technical question about {company}'s architecture, monitoring, or stack",
    "Operational question about standby rotations, SLA targets, or team collaboration",
    "Strategic question on upcoming migrations, tool modernizations, or 6-month roadmap",
    "Success measurement question for the first 90 days"
  ],
  "salary_guidance": "Recommended monthly gross range in € (e.g. €4,200 - €4,800/mo) grounded in Finnish IT sector standards.",
  "practical_details": "Key details on immediate 0-day notice, Finnish driving license, Supo clearance, and references."
}}
"""

    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2
        }
    }
    payload = json.dumps(data).encode("utf-8")

    for model_name in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                if parsed.get("elevator_pitch") and parsed.get("star_scenario_1"):
                    print(f"🎯 [AI Interview Prep] Successfully generated prep guide using {model_name}!")
                    return parsed
        except Exception as e:
            print(f"⚠️ [AI Interview Prep] Notice on {model_name}: {e}")
            continue

    return None

