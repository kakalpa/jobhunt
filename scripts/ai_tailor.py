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
from datetime import datetime
from pathlib import Path

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", Path(__file__).resolve().parent.parent)).resolve()
sys.path.insert(0, str(WORKSPACE_DIR))
sys.path.insert(0, str(WORKSPACE_DIR / "scripts"))

try:
    from scripts.job_filters import get_candidate_contact_info
except ImportError:
    from job_filters import get_candidate_contact_info

class AITailoringError(Exception):
    """Exception raised when AI document tailoring encounters an API, configuration, or network error."""
    def __init__(self, message: str, is_api_error: bool = True):
        super().__init__(message)
        self.is_api_error = is_api_error

def get_key_from_env(name: str) -> str:
    """Load specified API key from process environment or .env file."""
    key = os.environ.get(name, "")
    if key:
        return key.strip().strip('"').strip("'")
    env_path = WORKSPACE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

def get_api_key() -> str:
    """Load Gemini API Key from environment or .env file."""
    return get_key_from_env("GEMINI_API_KEY")

def get_groq_key() -> str:
    """Load Groq API Key from environment or .env file."""
    return get_key_from_env("GROQ_API_KEY")

def get_openrouter_key() -> str:
    """Load OpenRouter API Key from environment or .env file."""
    return get_key_from_env("OPENROUTER_API_KEY")

def has_any_ai_key() -> bool:
    """Check if at least one AI API key (Gemini, Groq, or OpenRouter) is configured."""
    return bool(get_api_key() or get_groq_key() or get_openrouter_key())

MODELS = [
    "models/gemini-3.5-flash",
    "models/gemini-3-flash-preview"
]

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b"
]

OPENROUTER_MODELS = [
    "nex-agi/nex-n2.5-pro:free",
    "nex-agi/nex-n2.5-mini:free",
    "nvidia/nemotron-3.5-lightning:free"
]

AI_STATUS_FILE = WORKSPACE_DIR / ".ai_api_status.json"

def record_ai_api_status(status: str, error: str = "", model: str = ""):
    """Persist the health and error state of the AI API integration."""
    now_iso = datetime.now().isoformat()
    data = {
        "status": status,
        "last_error": error,
        "last_checked": now_iso,
        "model": model
    }
    if status == "ok":
        data["last_success"] = now_iso
    else:
        if AI_STATUS_FILE.exists():
            try:
                prev = json.loads(AI_STATUS_FILE.read_text(encoding="utf-8"))
                if prev.get("last_success"):
                    data["last_success"] = prev["last_success"]
            except Exception:
                pass
    try:
        AI_STATUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

def call_groq_json(prompt: str, api_key: str, timeout: int = 25) -> tuple:
    """Execute JSON generation against GroqCloud OpenAI-compatible endpoint."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    last_err = ""
    errors_by_model = []
    for model_name in GROQ_MODELS:
        payload = json.dumps({
            "model": model_name,
            "messages": [
                {"role": "system", "content": "You are an expert career consultant and document tailoring assistant. Return valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 4096
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                res = json.loads(response.read().decode("utf-8"))
                text = res["choices"][0]["message"]["content"]
                parsed = json.loads(text)
                return parsed, f"groq:{model_name}", ""
        except Exception as e:
            last_err = str(e)
            errors_by_model.append(f"{model_name}: {last_err}")
            print(f"⚠️ [Groq Failover] {model_name} error: {last_err}")
            continue
    return None, "", "; ".join(errors_by_model) if errors_by_model else last_err

def call_openrouter_json(prompt: str, api_key: str, timeout: int = 25) -> tuple:
    """Execute JSON generation against OpenRouter free endpoint."""
    url = "https://openrouter.ai/api/v1/chat/completions"
    last_err = ""
    errors_by_model = []
    for model_name in OPENROUTER_MODELS:
        payload = json.dumps({
            "model": model_name,
            "messages": [
                {"role": "system", "content": "You are an expert career consultant and document tailoring assistant. Return valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 4096
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://jobhunt.local",
                "X-Title": "JobHunt",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                res = json.loads(response.read().decode("utf-8"))
                text = res["choices"][0]["message"]["content"]
                parsed = json.loads(text)
                return parsed, f"openrouter:{model_name}", ""
        except Exception as e:
            last_err = str(e)
            errors_by_model.append(f"{model_name}: {last_err}")
            print(f"⚠️ [OpenRouter Failover] {model_name} error: {last_err}")
            continue
    return None, "", "; ".join(errors_by_model) if errors_by_model else last_err

def call_gemini_json(prompt: str, api_key: str = None, timeout: int = 20, max_retries: int = 1) -> tuple:
    """
    Executes a structured JSON generation request with automated multi-provider failover:
    1. Primary: Google Gemini models (gemini-3.5-flash, gemini-3-flash-preview)
    2. Failover 1: GroqCloud (openai/gpt-oss-120b, openai/gpt-oss-20b, qwen/qwen3.8-27b)
    3. Failover 2: OpenRouter Free Models
    Returns (parsed_dict, model_used, error_msg).
    """
    import time
    gemini_key = (api_key or get_api_key()).strip()
    groq_key = get_groq_key()
    openrouter_key = get_openrouter_key()
    
    last_err_msg = ""
    failover_trace = []

    # --- 1. Try Google Gemini Primary ---
    if gemini_key:
        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2
            }
        }
        payload = json.dumps(data).encode("utf-8")

        for model_name in MODELS:
            short_name = model_name.replace("models/", "")
            for attempt in range(max_retries):
                url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={gemini_key}"
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as response:
                        raw_bytes = response.read().decode("utf-8")
                        res = json.loads(raw_bytes)
                        candidates = res.get("candidates", [])
                        if not candidates:
                            raise ValueError("No candidates returned in Gemini API response")
                        text = candidates[0]["content"]["parts"][0]["text"]
                        parsed = json.loads(text)
                        record_ai_api_status("ok", "", model_name)
                        parsed["_ai_meta"] = {
                            "engine": f"Google Gemini ({short_name})",
                            "provider": "gemini",
                            "model": model_name,
                            "failover_trace": list(failover_trace),
                            "summary": f"Generated via Google Gemini ({short_name})"
                        }
                        return parsed, model_name, ""
                except urllib.error.HTTPError as e:
                    last_err_msg = f"HTTP Error {e.code}: {e.reason}"
                    failover_trace.append(f"Gemini ({short_name}): {last_err_msg}")
                    print(f"⚠️ [Gemini Client] {model_name} attempt {attempt+1}/{max_retries}: {last_err_msg}")
                    break
                except Exception as e:
                    last_err_msg = str(e)
                    failover_trace.append(f"Gemini ({short_name}): {last_err_msg}")
                    print(f"⚠️ [Gemini Client] {model_name} attempt {attempt+1}/{max_retries}: {last_err_msg}")
                    break
    else:
        failover_trace.append("Google Gemini: Key not configured")

    # --- 2. Failover to Groq ---
    if groq_key:
        print(f"🔄 [AI Failover] Switching to Groq fallback engine...")
        parsed, model_used, groq_err = call_groq_json(prompt, groq_key, timeout=min(timeout, 45))
        if parsed:
            record_ai_api_status("ok", "", model_used)
            groq_model_name = model_used.replace("groq:", "")
            parsed["_ai_meta"] = {
                "engine": f"Groq ({groq_model_name})",
                "provider": "groq",
                "model": groq_model_name,
                "failover_trace": list(failover_trace),
                "summary": f"Generated via Groq ({groq_model_name})" + (f" (after {failover_trace[0]})" if failover_trace else "")
            }
            return parsed, model_used, ""
        failover_trace.append(f"Groq: {groq_err}")
        last_err_msg = f"Groq failover error: {groq_err} (Previous Gemini error: {last_err_msg})"
    else:
        failover_trace.append("Groq: Key not configured")

    # --- 3. Failover to OpenRouter ---
    if openrouter_key:
        print(f"🔄 [AI Failover] Switching to OpenRouter fallback engine...")
        parsed, model_used, or_err = call_openrouter_json(prompt, openrouter_key, timeout=min(timeout, 45))
        if parsed:
            record_ai_api_status("ok", "", model_used)
            or_model_name = model_used.replace("openrouter:", "")
            parsed["_ai_meta"] = {
                "engine": f"OpenRouter ({or_model_name})",
                "provider": "openrouter",
                "model": or_model_name,
                "failover_trace": list(failover_trace),
                "summary": f"Generated via OpenRouter ({or_model_name})" + (f" (after {failover_trace[0]})" if failover_trace else "")
            }
            return parsed, model_used, ""
        failover_trace.append(f"OpenRouter: {or_err}")
        last_err_msg = f"OpenRouter failover error: {or_err} (Previous error: {last_err_msg})"
    else:
        failover_trace.append("OpenRouter: Key not configured")

    # --- All Providers Failed ---
    all_err_summary = "All AI engines failed: " + " → ".join(failover_trace)
    record_ai_api_status("error", all_err_summary)
    try:
        from scripts.telegram_notifier import notify_api_key_error
        notify_api_key_error("AI Inference Engine", all_err_summary)
    except Exception:
        pass

    return None, "", all_err_summary

def get_ai_api_status() -> dict:
    """Retrieve current AI API health state."""
    if not has_any_ai_key():
        return {"status": "not_configured", "configured": False, "last_error": "No AI API keys configured (Gemini, Groq, or OpenRouter)."}
    
    if AI_STATUS_FILE.exists():
        try:
            data = json.loads(AI_STATUS_FILE.read_text(encoding="utf-8"))
            data["configured"] = True
            return data
        except Exception:
            pass
    return {"status": "unverified", "configured": True, "last_error": ""}

def check_gemini_api_key(test_key: str = None) -> dict:
    """Validate API key directly against Google Generative Language models endpoint."""
    key = (test_key or get_api_key()).strip()
    if not key:
        # Check if Groq is available instead
        groq_k = get_groq_key()
        if groq_k:
            return check_groq_api_key(groq_k)
        record_ai_api_status("not_configured", "Missing GEMINI_API_KEY")
        return {"valid": False, "status": "not_configured", "error": "No GEMINI_API_KEY configured in environment or .env"}

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            if response.status == 200:
                record_ai_api_status("ok", "", "gemini-models-endpoint")
                return {"valid": True, "status": "ok", "provider": "gemini", "error": None, "message": "Google Gemini API key is active and authorized."}
    except urllib.error.HTTPError as e:
        err_msg = f"HTTP Error {e.code}: {e.reason}"
        record_ai_api_status("error", err_msg)
        return {"valid": False, "status": "error", "error": err_msg, "message": f"Gemini API rejected key: {err_msg}"}
    except Exception as e:
        err_msg = str(e)
        record_ai_api_status("error", err_msg)
        return {"valid": False, "status": "error", "error": err_msg, "message": f"Network error connecting to Gemini API: {err_msg}"}

def check_groq_api_key(test_key: str = None) -> dict:
    """Validate Groq API key directly against models endpoint."""
    key = (test_key or get_groq_key()).strip()
    if not key:
        return {"valid": False, "status": "not_configured", "error": "No GROQ_API_KEY configured"}
    url = "https://api.groq.com/openai/v1/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "JobHunt/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            if response.status == 200:
                return {"valid": True, "status": "ok", "provider": "groq", "error": None, "message": "Groq API key is active and authorized."}
    except Exception as e:
        return {"valid": False, "status": "error", "error": str(e), "message": f"Groq API error: {e}"}

def check_openrouter_api_key(test_key: str = None) -> dict:
    """Validate OpenRouter API key directly against models endpoint."""
    key = (test_key or get_openrouter_key()).strip()
    if not key:
        return {"valid": False, "status": "not_configured", "error": "No OPENROUTER_API_KEY configured"}
    url = "https://openrouter.ai/api/v1/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "JobHunt/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            if response.status == 200:
                return {"valid": True, "status": "ok", "provider": "openrouter", "error": None, "message": "OpenRouter API key is active and authorized."}
    except Exception as e:
        return {"valid": False, "status": "error", "error": str(e), "message": f"OpenRouter API error: {e}"}

def synthesize_fallback_job_description(title: str, company: str, location: str, brief_note: str = "") -> str:
    """
    Synthesizes a comprehensive, high-fidelity baseline job description
    when web scraping is blocked by anti-bot walls (e.g. Indeed, LinkedIn authwall) or no raw text was provided.
    Guarantees >600 characters so AI tailoring can contextualize the application properly.
    """
    t_clean = (title or "IT Specialist").strip()
    c_clean = (company or "Enterprise Partner").strip()
    l_clean = (location or "Finland").strip()
    
    note_part = f"\nSpecific Posting Notes:\n{brief_note.strip()}\n" if brief_note and len(brief_note.strip()) > 10 else ""
    
    return (
        f"Position: {t_clean}\n"
        f"Company: {c_clean}\n"
        f"Location: {l_clean}\n"
        f"{note_part}\n"
        f"Role Summary & Operational Scope:\n"
        f"We are seeking a proactive and skilled {t_clean} to join {c_clean}'s engineering and IT operations in {l_clean}. "
        f"The successful candidate will take ownership of maintaining enterprise systems infrastructure, ensuring high operational uptime, "
        f"driving automated workflow improvements, and providing tier-2/3 technical escalation support in alignment with organizational SLAs.\n\n"
        f"Core Technical Responsibilities:\n"
        f"- Administer, configure, and troubleshoot enterprise systems across Linux (RHEL/Ubuntu) and Windows Server environments.\n"
        f"- Manage cloud and hybrid infrastructure, identity governance (Microsoft Entra ID, Active Directory, M365), and endpoint device policies.\n"
        f"- Develop modular automation scripts using Python, Bash, or PowerShell to streamline recurring operational tasks.\n"
        f"- Execute hardware break-fix, structured network troubleshooting (TCP/IP, VLANs, Firewalls, VPNs), and system upgrades.\n"
        f"- Implement ITIL-aligned incident management, participate in Change Advisory Board (CAB) reviews, and maintain accurate CMDB documentation.\n"
        f"- Collaborate closely with internal stakeholders and cross-functional technology teams to ensure robust information security compliance."
    )

def tailor_application(title: str, company: str, location: str, jd_text: str, strict: bool = False) -> dict:
    """
    Invokes Gemini to analyze the job posting and tailor the candidate's application
    using all specialized job search skills.
    Returns a dictionary of tailored sections or raises AITailoringError if strict=True.
    """
    if not has_any_ai_key():
        record_ai_api_status("not_configured", "Missing AI API keys (Gemini, Groq, or OpenRouter)")
        if strict:
            raise AITailoringError("No AI API keys configured in environment or .env. Please configure your key in Settings.")
        return None

    cleaned_jd = (jd_text or "")[:4500].strip()
    if len(cleaned_jd) < 100:
        print(f"ℹ️ [AI Tailor] Job description was short ({len(cleaned_jd)} chars); auto-enriching with synthesized role profile for '{title}' @ '{company}'...")
        cleaned_jd = synthesize_fallback_job_description(title, company, location, cleaned_jd)

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
   - 4-pillar expanded structure: Opening hook tailored to {company}'s environment, Core technical match with quantifiable results, Operational track record & metrics (94% FTF, 200+ endpoints, ITIL governance, automation scripts, 2026 hackathon win), and authentic motivation for {company}.
   - Total cover letter body should be comprehensive, persuasive, and substantial (320-400 words total). Avoid generic 'I am writing to apply for...'. No salutations or sign-offs.

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
  "cv_location": "Strategic location line. If {location} is outside Turku/Helsinki (e.g. Kajaani, Oulu, Tampere), output '{location}, Finland (Immediate Relocation Ready | Turku/Helsinki Base)'. If Helsinki/Turku, output 'Helsinki Metropolitan Area / Turku, Finland'.",
  "cv_summary": "2-3 sentence impactful professional summary highlighting direct overlap with {company}'s priorities and {title} requirements.",
  "cv_skills_block": "4-5 structured markdown lines formatted as '* **[Category]:** [Keywords...]' specifically prioritizing the exact technologies and responsibilities mentioned in the JD.",
  "cv_mainframe_bullets": [
    "Accomplished [X] as measured by [Y], by doing [Z] bullet 1 for Mainframe (Pvt) Limited targeting this job's core technical priorities...",
    "Accomplished [X] as measured by [Y], by doing [Z] bullet 2 for Mainframe (Pvt) Limited...",
    "Accomplished [X] as measured by [Y], by doing [Z] bullet 3 for Mainframe (Pvt) Limited...",
    "Accomplished [X] as measured by [Y], by doing [Z] bullet 4 for Mainframe (Pvt) Limited..."
  ],
  "cv_tuas_bullets": [
    "Accomplished [X] as measured by [Y], by doing [Z] bullet 1 for TUAS relevant to this position...",
    "Accomplished [X] as measured by [Y], by doing [Z] bullet 2 for TUAS...",
    "Won 1st Place Team & 2nd Place Individual in the 2026 DNCS Live-Fire Cybersecurity Hackathon..."
  ],
  "cv_certifications": [
    "Certification 1 (ordered by highest relevance to {title})",
    "Certification 2",
    "Certification 3",
    "Certification 4",
    "Certification 5"
  ],
  "cv_custom_bullets": [
    "Accomplished [X] as measured by [Y], by doing [Z] bullet 1 targeting {company}'s stack...",
    "Accomplished [X] as measured by [Y], by doing [Z] bullet 2 targeting {company}'s stack..."
  ],
  "cover_letter_body": "4 substantive body paragraphs (320-400 words total) combining: 1) Strong opening hook connecting candidate to {company}'s mission, 2) Deep hands-on technical alignment with JD technologies, 3) Real operational metrics (94% FTF rate, 200+ endpoints, ITIL governance, automation in Python/Bash/PowerShell, 2026 hackathon win), and 4) Genuine motivation for why {company} is the candidate's top choice. Do NOT include salutations, dates, or sign-offs.",
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

    parsed, model_used, err_msg = call_gemini_json(prompt, timeout=25)
    if parsed and parsed.get("cv_summary") and parsed.get("cover_letter_body"):
        meta = parsed.get("_ai_meta", {})
        engine_str = meta.get("engine", model_used)
        print(f"✨ [AI Tailor] Successfully tailored application using {engine_str} with all 12 skills!")
        return parsed

    if strict:
        raise AITailoringError(f"{err_msg or 'Failed to generate tailored sections'}")

    return None

def generate_ai_interview_prep(title: str, company: str, location: str, jd_text: str) -> dict:
    """
    Invokes Gemini to generate comprehensive Interview Preparation & Systematic CV Walk-Through
    incorporating interview-prep-generator, finnish-job-market-tailor, and salary-negotiation-prep.
    """
    if not has_any_ai_key():
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

    parsed, model_used, err_msg = call_gemini_json(prompt, timeout=25)
    if parsed and parsed.get("elevator_pitch") and parsed.get("star_scenario_1"):
        print(f"🎯 [AI Interview Prep] Successfully generated prep guide using {model_used}!")
        return parsed

    return None

def generate_expanded_cover_letter(title: str, company: str, location: str = "Finland", jd_text: str = "", is_finnish: bool = False) -> dict:
    """
    Generates a comprehensive, high-converting 4-pillar expanded cover letter (~350–450 words)
    adhering to cover-letter-generator and finnish-job-market-tailor methodologies.
    Returns a dictionary with full_markdown, word_count, and metadata.
    """
    from datetime import datetime
    import re

    cand = get_candidate_contact_info(WORKSPACE_DIR)
    cand_name = cand["name"]
    cand_phone = cand["phone"]
    cand_email = cand["email"]
    cand_linkedin = cand["linkedin"]
    cand_location = cand["location"]

    today_en = datetime.now().strftime("%B %d, %Y")
    today_fi = f"{datetime.now().day}.{datetime.now().month}.{datetime.now().year}"
    date_str = today_fi if is_finnish else today_en

    cleaned_jd = (jd_text or "")[:4500].strip()
    title_lower = (title or "").lower()
    jd_lower = cleaned_jd.lower()

    is_security = any(k in title_lower or k in jd_lower for k in ("security", "soc", "cyber", "threat", "siem", "incident", "pentest", "vulnerability"))
    is_devops = any(k in title_lower or k in jd_lower for k in ("devops", "cloud", "platform", "sre", "kubernetes", "docker", "ci/cd", "terraform", "ansible"))
    is_datacenter = any(k in title_lower or k in jd_lower for k in ("data center", "datacenter", "hardware", "cabling", "rack", "dl380", "bare metal", "server hardware"))

    def build_deterministic_cover_letter():
        if is_finnish:
            if is_security:
                tech_p = "Tekninen ydinosaamiseni keskittyy ennakoivaan uhkien havainnointiin, Zero Trust -perusteiseen pääsynhallintaan ja automatisoituun poikkeamien hallintaan. Minulla on käytännön kokemusta Microsoft Defender XDR-, Sentinel SIEM (KQL)- ja Wazuh-järjestelmien ylläpidosta ja hälytysten analysoinnista. Olen suorittanut TryHackMe SOC Level 1- ja PenTest+ -sertifioinnit sekä luonut automatisoituja vasteajoputkia PowerShellin ja Microsoft Graph API:n avulla yhdistäen tarkan teknisen tutkinnan ennaltaehkäisevään ajattelutapaan."
            elif is_devops:
                tech_p = "Operatiivisessa työssäni yhdistän tuotantotason Linux-palvelinympäristöjen (RHEL, Ubuntu) hallinnan, Docker-kontituksen sekä Azure-pilvi-infrastruktuurin. Automatisoin rutiinitehtäviä ja käyttöönottoprosesseja Ansiblella, Pythonilla ja Bashilla, mikä on vähentänyt manuaalista työtä ja virhemahdollisuuksia merkittävästi ja varmistanut 99.9 %:n palvelukäytettävyyden."
            elif is_datacenter:
                tech_p = "Laitteisto- ja konesaliympäristöissä vahvuuteni ovat fyysinen asennustarkkuus, bare-metal-palvelinten provisiointi (HPE ProLiant DL20/DL380 Gen9/Gen10), komponenttitason diagnostiikka ja strukturoitu kaapelointi ESD-standardeja noudattaen. Hallitsen iLO-etähallinnan, älykkäät PDU-virranjakelut sekä nopeat komponenttien vaihdot."
            else:
                tech_p = "Tekninen ydinosaamiseni kattaa laajat monialustaiset työasemaympäristöt ja identiteetinhallinnan. Olen vastannut yli 200 monikäyttöjärjestelmäisen päätelaitteen (Windows 10/11, macOS, Linux) elinkaaresta sekä hallinnoinut yli 300 käyttäjän Microsoft 365- ja Entra ID (Azure AD) -kokonaisuuksia Intune MDM -vaatimustenmukaisuuden mukaisesti. Hallitsen Active Directoryn (AD DS, GPO, RBAC), HPE ProLiant -palvelinlaitteistojen vianrajauksen sekä yritysverkkojen perusrakenteet (TCP/IP, VLAN, DNS, DHCP)."

            hook_p = f"Yritysten toimintavarmuuden ja modernin IT-infrastruktuurin merkityksen korostuessa olin erittäin innostunut huomaamaan **{title}** -tehtävänne **{company}**lla. Valmistuttuani tietotekniikan insinööriksi Turun ammattikorkeakoulusta (TUAS, GPA 4.0 / 4.0) ja kerrytettyäni yli 8 vuoden monipuolisen käytännön kokemuksen yritysten järjestelmäylläpidosta, pilvi-infrasta sekä operatiivisesta tuesta, tarjoan tiimillenne välittömästi tuottavan ja ennaltaehkäisevään ylläpitoon sitoutuneen vahvistuksen."

            ops_p = "Operatiivinen täsmällisyys ja järjestelmällisyys ohjaavat kaikkea tekemistäni. Toimiessani apulaistiiminvetäjänä (Associate Tech Lead) saavutin 94 %:n ensiratkaisuasteen (First-Time-Fix) korkeavolyymisissa tukijonoissa ITIL-prosessien ja CAB-muutoshallinnan puitteissa. Ennaltaehkäisevänä insinöörinä kehitän automaatiota toistuvien häiriöiden pysyvään poistamiseen, mikä on vähentänyt toistuvia tukipyyntöjä jopa 40 %. Lisäksi voitto vuoden 2026 DNCS Live-Fire -kyberhackathonissa osoittaa kykyni toimia rauhallisesti ja tehokkaasti vaativissakin teknisissä vikatilanteissa."

            why_p = f"Minua houkuttelee **{company}**ssa erityisesti sitoutumisenne korkeaan teknologiseen laatuun, luotettaviin palveluihin sekä moderniin insinöörikulttuuriin. Viihdyn suomalaisessa matalahierarkkisessa työkulttuurissa, jossa arvostetaan vastuunottoa, selkeää dokumentaatiota ja jatkuvaa ammatillista kehittymistä. Tehtävä tarjoaa minulle loistavan mahdollisuuden tuoda osaamiseni osaksi {location}n tiimiänne."

            closing_p = f"Asun pysyvästi Suomessa ja minulla on EU-työlupa, minkä ansiosta voin aloittaa välittömästi (0 päivän irtisanomisaika). Olen täysin valmis Supon perusmuotoiseen turvallisuusselvitykseen. Työskentelen sujuvasti englanniksi (C1) ja kehitän aktiivisesti käytännön suomen kielen taitoani arjen työyhteisöviestintää varten. Keskustelen mielelläni tarkemmin siitä, miten kokemukseni voi tukea {company}n tavoitteita."

            md = f"""# {cand_name}
{cand_location} | {cand_phone} | {cand_email} | [LinkedIn]({cand_linkedin})

{date_str}

**{company}** | {location}  
**Aihe: Hakemus tehtävään: {title}**

Hei {company} tiimi,

{hook_p}

{tech_p}

{ops_p}

{why_p}

{closing_p}

Ystävällisin terveisin,  
**{cand_name}**
"""
        else:
            if is_security:
                tech_p = f"Throughout my background in enterprise systems and security operations, I focus on proactive threat containment, robust identity governance, and automated incident triage. My hands-on technical stack centers on Microsoft Defender XDR, Microsoft Sentinel (KQL), Wazuh SIEM, and Splunk for threat detection, incident triage, and root-cause analysis. I administer Zero Trust perimeters across Entra ID (Conditional Access, MFA, RBAC), map active threats against the MITRE ATT&CK framework, and author automated remediation playbooks using PowerShell, Python, and the Microsoft Graph API. Holding verifiable TryHackMe SOC Level 1 and PenTest+ certifications, I combine disciplined technical investigation with a prevention-first mindset."
            elif is_devops:
                tech_p = f"In business-critical environments where platform automation and 99.9% uptime are vital, my technical practice bridges Linux systems engineering with modern cloud infrastructure. I have extensive hands-on experience administering production Red Hat (RHEL) and Ubuntu Linux environments, orchestrating containerized workloads with Docker, and provisioning scalable cloud resources in Microsoft Azure. By authoring modular Infrastructure-as-Code (IaC) configurations and deployment automation in Ansible, Python, and Bash, I systematically eliminate configuration drift and reduce provisioning turnaround time by over 60%."
            elif is_datacenter:
                tech_p = f"Across eight years of physical and hardware operations, I specialize in bare-metal server infrastructure, component-level hardware diagnostics, and structured cabling under rigorous ESD protocols. I have provisioned, racked, and cabled HPE ProLiant (DL20/DL380 Gen9/Gen10) enterprise servers, managed out-of-band telemetry via iLO, and maintained intelligent PDUs and UPS distribution. I excel at rapid component replacements (CPUs, RAM, SAS RAID arrays, hot-swap backplanes) and methodical change control, ensuring high physical availability and zero unmonitored hardware faults."
            else:
                tech_p = f"My core technical foundation encompasses enterprise workplace ecosystems, hybrid identity, and multi-OS endpoint management. I have administered fleets of over 200 workstations (Windows 10/11, macOS, and Linux) alongside 300+ user Microsoft 365 and Entra ID (Azure AD) tenants with Intune MDM compliance policies. My experience spans Active Directory (AD DS, Group Policy, RBAC), enterprise peripheral integration, bare-metal server break-fix (HPE DL20/DL380), and network infrastructure troubleshooting across TCP/IP, VLANs, DNS, and DHCP."

            hook_p = f"With modern organizations increasingly prioritizing operational resilience and cloud continuity, I was excited to discover the **{title}** opening at **{company}**. Combining a Bachelor of Engineering in Information Technology from Turku University of Applied Sciences (TUAS, 4.0 / 4.0 GPA) with over eight years of progressive hands-on experience in enterprise systems administration, cloud infrastructure, and operational reliability, I am eager to deliver immediate reliability and operational excellence to your team."

            ops_p = "Operational rigor and proactive prevention are central to how I work. In my role as Associate Tech Lead, I maintained a 94% First-Time-Fix rate across 200+ multi-OS workstations and 300+ user tenants while upholding strict ITIL SLA commitments and CAB change management governance. Rather than repeatedly resolving the same operational friction, I develop modular automation scripts in Python, Bash, and PowerShell that have reduced routine administrative overhead by 40%. Furthermore, earning 1st Place in the 2026 DNCS Live-Fire Cybersecurity Hackathon demonstrated my capacity to troubleshoot intricate technical environments, isolate cascading faults, and deliver dependable solutions under pressure."

            why_p = f"What draws me specifically to **{company}** is your reputation for high engineering standards, operational dependability, and forward-looking technical vision. I thrive in collaborative Nordic workplace cultures that champion technical ownership, clear documentation, and continuous professional growth. Contributing my background to support your technical operations and strategic roadmap in {location} offers the ideal environment where my dedication to preventative engineering and long-term service stability will create lasting value."

            closing_p = f"Based permanently in Finland with full EU work authorization, I offer immediate 0-day notice availability and am fully prepared for standard security clearance (perusmuotoinen turvallisuusselvitys) and reference verifications. I communicate fluently in English (C1) and am actively advancing my practical Finnish for everyday workplace communication. I welcome the opportunity to discuss how my technical depth, operational discipline, and customer-first mindset align with {company}'s objectives."

            md = f"""# {cand_name}
{cand_location} | {cand_phone} | {cand_email} | [LinkedIn]({cand_linkedin})

{date_str}

**{company}** | {location}  
**RE: Application for {title}**

Dear {company} Hiring Team,

{hook_p}

{tech_p}

{ops_p}

{why_p}

{closing_p}

Sincerely,  
**{cand_name}**
"""
        return {
            "title": title,
            "company": company,
            "location": location,
            "is_finnish": is_finnish,
            "full_markdown": md,
            "word_count": len(md.split()),
            "generated_by": "deterministic_expanded_engine"
        }

    if has_any_ai_key() and len(cleaned_jd) > 80:
        lang_directive = "Author the body paragraphs in natural, idiomatic, professional Finnish (hakemuskirje)." if is_finnish else "Author the body paragraphs in clear, polished, authoritative business English."
        salutation = f"Hei {company} tiimi," if is_finnish else f"Dear {company} Hiring Team,"
        signoff = "Ystävällisin terveisin," if is_finnish else "Sincerely,"
        re_label = f"Aihe: Hakemus tehtävään: {title}" if is_finnish else f"RE: Application for {title}"

        prompt = f"""You are an executive career advisor and technical cover letter specialist for {cand_name}, an IT systems & infrastructure engineer in Finland.

Generate a rich, comprehensive 4-pillar expanded cover letter body for:
COMPANY: {company}
ROLE: {title}
LOCATION: {location}
JOB DESCRIPTION & REQUIREMENTS:
{cleaned_jd}

---
CANDIDATE BASE DATA:
- Name: {cand_name}
- Education: B.Eng. in Information Technology, Turku University of Applied Sciences (TUAS), GPA 4.0 / 4.0.
- Experience: 8+ years hands-on enterprise systems administration, Linux (RHEL/Ubuntu), Windows Server, Active Directory, Azure, M365, Entra ID, Intune, bare-metal hardware (HPE ProLiant DL20/DL380), automation (Python, Bash, PowerShell).
- Track Record: Associate Tech Lead at Mainframe (200+ endpoints, 94% First-Time-Fix rate, CAB change management). 1st Place in 2026 DNCS Cyber Hackathon.
- Finnish Grounding: Full EU Work Authorization, resident in Finland, 0-day notice period, ready for Supo security clearance. English: C1 (fluent), Finnish: conversational/actively advancing.

---
INSTRUCTIONS:
{lang_directive}
Produce a STRICT JSON object containing:
{{
  "hook_paragraph": "1 strong opening hook (3-4 sentences) connecting candidate's TUAS 4.0 GPA and 8+ years enterprise background directly to {company}'s specific operational priorities. Avoid generic 'I am writing to apply'.",
  "tech_pillar_paragraph": "1 substantive paragraph (4-5 sentences) showing deep hands-on proficiency in the core technical platforms and tools requested in the JD.",
  "ops_pillar_paragraph": "1 impact-driven paragraph (4-5 sentences) highlighting real-world enterprise metrics: 94% FTF rate, 200+ multi-OS workstations, ITIL SLA discipline, Python/Bash/PowerShell automation, and 2026 hackathon win.",
  "why_company_paragraph": "1 authentic paragraph (3-4 sentences) articulating why {company} and this position are the candidate's top choice.",
  "closing_paragraph": "1 confident closing paragraph highlighting permanent EU work authorization, immediate 0-day notice, Supo clearance readiness, C1 English and practical Finnish."
}}
"""
        parsed, model_used, err_msg = call_gemini_json(prompt, timeout=25)
        if parsed and parsed.get("hook_paragraph") and parsed.get("tech_pillar_paragraph"):
            md = f"""# {cand_name}
{cand_location} | {cand_phone} | {cand_email} | [LinkedIn]({cand_linkedin})

{date_str}

**{company}** | {location}  
**{re_label}**

{salutation}

{parsed["hook_paragraph"].strip()}

{parsed["tech_pillar_paragraph"].strip()}

{parsed.get("ops_pillar_paragraph", "").strip()}

{parsed.get("why_company_paragraph", "").strip()}

{parsed.get("closing_paragraph", "").strip()}

{signoff}  
**{cand_name}**
"""
            return {
                "title": title,
                "company": company,
                "location": location,
                "is_finnish": is_finnish,
                "full_markdown": md,
                "word_count": len(md.split()),
                "generated_by": f"gemini ({model_used})"
            }

    return build_deterministic_cover_letter()

def generate_top_choice_pitch(title: str, company: str, location: str = "Finland", jd_text: str = "") -> dict:
    """
    Generates tailored pitches explaining:
    1. Why the candidate is the top choice for this role (Candidate Pitch for Recruiter / Easy Apply)
    2. Why this job & company is the candidate's #1 choice (Motivation Statement)
    3. Quick LinkedIn connection request note (< 300 chars)
    4. Full LinkedIn post / share draft
    """
    import re
    cand = get_candidate_contact_info(WORKSPACE_DIR)
    cand_name = cand["name"]
    cleaned_jd = (jd_text or "")[:4500].strip()

    tech_catalog = {
        "azure": "Microsoft Azure cloud services & hybrid infrastructure",
        "m365": "Microsoft 365, Entra ID & Intune endpoint management",
        "active directory": "Active Directory (AD DS) & hybrid identity provisioning",
        "windows server": "Windows Server administration & enterprise services",
        "linux": "Linux enterprise server administration (RHEL, Ubuntu, Rocky Linux)",
        "powershell": "PowerShell automation & administrative scripting",
        "python": "Python automation scripting & REST API integrations",
        "docker": "Docker containerization & deployment pipelines",
        "kubernetes": "Kubernetes cluster operations & cloud-native workflows",
        "wazuh": "Wazuh SIEM host monitoring & endpoint compliance",
        "siem": "SIEM security event correlation & incident triage",
        "sentinel": "Microsoft Sentinel cloud-native SIEM & threat hunting",
        "itil": "ITIL service desk operations, SLA discipline & CAB change control",
        "ansible": "Ansible configuration management & infrastructure as code",
        "terraform": "Terraform cloud infrastructure provisioning",
        "vmware": "VMware ESXi & vSphere virtualization management",
        "proxmox": "Proxmox VE virtualization & high-availability clustering",
        "cisco": "Cisco networking, VLANs, switching & routing protocols",
        "firewall": "Enterprise firewall configuration & network perimeter security",
        "servicenow": "ServiceNow incident, request & CMDB management",
        "jira": "Jira Service Management & agile issue tracking"
    }

    jd_low = cleaned_jd.lower()
    matched_tech = [desc for kw, desc in tech_catalog.items() if kw in jd_low]
    if not matched_tech:
        matched_tech = [
            "Hybrid Azure & Microsoft 365 enterprise administration",
            "Windows Server & Linux multi-platform operations",
            "PowerShell & Python operational automation",
            "ITIL-aligned incident management and SLA delivery"
        ]

    def build_deterministic_pitch():
        top_skills_preview = ", ".join([kw.capitalize() for kw in list(tech_catalog.keys()) if kw in jd_low][:3])
        if not top_skills_preview:
            top_skills_preview = "Azure, M365, and Systems Automation"

        clean_comp_tag = re.sub(r'[^a-zA-Z0-9]', '', company)

        why_candidate = (
            f"Why I'm the top choice for {company}'s {title}: With a 4.0 GPA in ICT (TUAS) and 8+ yrs in enterprise infrastructure ({top_skills_preview}), I deliver high availability and automation. At Mainframe, I achieved a 94% First-Time-Fix rate across 200+ endpoints. Based in Finland with EU authorization and 0-day notice, I can make an immediate, turnkey impact."
        )
        if len(why_candidate) > 395:
            why_candidate = (
                f"Top choice for {company}'s {title}: 4.0 GPA in ICT (TUAS) + 8+ yrs enterprise infra ({top_skills_preview}). Delivered a 94% First-Time-Fix rate across 200+ endpoints with Python/Bash automation. Turnkey hire in Finland: permanent EU authorization, Supo-ready, and 0-day notice."
            )
        if len(why_candidate) > 400:
            why_candidate = why_candidate[:397].rsplit(" ", 1)[0] + "..."

        why_company = (
            f"💡 Why {company} is My #1 Top Choice:\n\n"
            f"{company} stands out as an exceptional organization where technological reliability and modern engineering standards drive measurable impact. The {title} role is a natural next step for my background, giving me the opportunity to deploy my expertise in {matched_tech[0]} and {matched_tech[1] if len(matched_tech) > 1 else 'operational automation'}.\n\n"
            f"I am specifically energized by your focus on scalable systems and high service availability. Collaborating with {company}'s team in {location} offers the ideal environment where my dedication to preventative engineering, zero-downtime operations, and continuous learning will deliver immediate and lasting value."
        )

        quick_connect = (
            f"Hi! I'm an IT systems & infrastructure engineer based in Finland (TUAS B.Eng., 4.0 GPA). "
            f"I saw the {title} role at {company} and wanted to reach out. "
            f"With 8+ yrs in enterprise infra ({top_skills_preview}), 94% first-time-fix rate, 0-day notice, and permanent EU work authorization, I'd love to connect and discuss how I can support your team!"
        )
        if len(quick_connect) > 395:
            quick_connect = (
                f"Hi! I'm an IT systems engineer based in Finland (TUAS 4.0 GPA, 8+ yrs infra). "
                f"I saw the {title} role at {company} and would love to connect! "
                f"With hands-on expertise in {top_skills_preview}, 0-day notice, and permanent EU authorization, I'm eager to contribute to your team."
            )
        if len(quick_connect) > 400:
            quick_connect = quick_connect[:397].rsplit(" ", 1)[0] + "..."

        post_draft = (
            f"🚀 Why I'm targeting the {title} opportunity at {company}:\n\n"
            f"As an IT systems and infrastructure engineer based in Finland (B.Eng. TUAS, 4.0 GPA), I am drawn to teams where operational resilience and modern architecture make a real difference.\n\n"
            f"Here is why this role at {company} is a standout alignment with my technical background:\n"
            f"🔹 {matched_tech[0]}\n"
            f"🔹 {matched_tech[1] if len(matched_tech) > 1 else 'Automation scripting with PowerShell & Python'}\n"
            f"🔹 {matched_tech[2] if len(matched_tech) > 2 else 'ITIL-aligned incident response & high-availability systems'}\n\n"
            f"With 8+ years of hands-on enterprise systems experience, permanent EU work authorization, and immediate 0-day notice availability, I'm excited to connect with anyone on the {company} team!\n\n"
            f"#{clean_comp_tag} #FinlandTech #ITOperations #CloudSecurity #DevOps #Helsinki #Turku"
        )

        return {
            "title": title,
            "company": company,
            "location": location,
            "why_top_choice_candidate": why_candidate,
            "why_top_choice_company": why_company,
            "linkedin_quick_pitch": quick_connect,
            "linkedin_post_draft": post_draft,
            "matched_skills": matched_tech[:5],
            "generated_by": "deterministic_engine"
        }

    if has_any_ai_key() and len(cleaned_jd) > 100:
        prompt = f"""You are an expert LinkedIn Career Coach and Executive Pitch Specialist for {cand_name}, an experienced IT systems and infrastructure engineer based in Finland.

COMPANY: {company}
POSITION: {title}
LOCATION: {location}
JOB DESCRIPTION / TECHNICAL REQUIREMENTS:
{cleaned_jd}

---
CANDIDATE FACTUAL PROFILE ({cand_name}):
- Education: B.Eng. in Information Technology, Turku University of Applied Sciences (TUAS), 4.0 / 4.0 GPA.
- Experience: 8+ years hands-on enterprise systems administration, bare-metal server infrastructure (HPE DL20/DL380), hybrid cloud (Azure, M365, Entra ID, Intune), Linux (RHEL, Ubuntu), virtualization, security operations (Wazuh SIEM, Sentinel), automation (Python, Bash, PowerShell).
- Track Record: Associate Tech Lead at Mainframe (200+ endpoints, 94% First-Time-Fix rate, CAB change management). 1st Place in 2026 DNCS Cyber Hackathon.
- Finnish Grounding: Permanent EU Work Authorization, immediate 0-day notice availability, resident in Finland, prepared for Supo security clearance. Fluent English (C1), working Finnish.

---
TASK:
Generate a specialized, high-converting LinkedIn Pitch Package in strict JSON format with these exact keys:
{{
  "why_top_choice_candidate": "Punchy, high-impact recruiter pitch strictly under 400 characters (aim for 320-390 characters). Hook with {title} & {company}, TUAS 4.0 GPA, 8+ yrs enterprise infra ({cand_name}'s key matching tech), 94% first-time-fix rate, permanent EU work authorization, and 0-day notice.",
  "why_top_choice_company": "Compelling, authentic 2-paragraph motivation statement explaining why {company} and this role are {cand_name}'s top choice, referencing company context from the JD.",
  "linkedin_quick_pitch": "Concise, high-converting LinkedIn pitch strictly under 400 characters (aim for 320-390 characters). Punchy hook citing role title and company, TUAS 4.0 GPA, key tech match from JD, 0-day notice, and permanent EU work authorization.",
  "linkedin_post_draft": "Ready-to-publish professional LinkedIn post with emojis, key alignment bullets, and hashtags.",
  "matched_skills": ["Top 4-5 technical skills extracted from JD that match the candidate"]
}}
"""
        parsed, model_used, err_msg = call_gemini_json(prompt, timeout=25)
        if parsed and parsed.get("why_top_choice_candidate") and parsed.get("linkedin_quick_pitch"):
            c = str(parsed["why_top_choice_candidate"]).strip()
            if len(c) > 400:
                c = c[:397].rsplit(" ", 1)[0] + "..."
                parsed["why_top_choice_candidate"] = c
            q = str(parsed["linkedin_quick_pitch"]).strip()
            if len(q) > 400:
                q = q[:397].rsplit(" ", 1)[0] + "..."
                parsed["linkedin_quick_pitch"] = q
            parsed["title"] = title
            parsed["company"] = company
            parsed["location"] = location
            parsed["generated_by"] = f"gemini ({model_used})"
            return parsed

    return build_deterministic_pitch()

