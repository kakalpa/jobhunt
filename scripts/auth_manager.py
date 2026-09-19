#!/usr/bin/env python3
"""
auth_manager.py - Enterprise Security, Authentication, Authorization & MFA Engine
Provides:
1. Scrypt/PBKDF2 Password Hashing & Verification (via werkzeug.security)
2. Native RFC 6238 TOTP Multi-Factor Authentication (compatible with Google Auth, Aegis, 1Password, etc.)
3. Emergency Recovery Codes generation & consumption
4. Brute-Force Rate Limiting (per-IP cooldown after 5 failed attempts)
5. Cryptographic CSRF Token issuance & verification
6. User Credential & MFA Configuration Management
"""

import os
import sys
import time
import json
import hmac
import struct
import base64
import hashlib
import secrets
from pathlib import Path
from typing import Tuple, Dict, List, Optional
from werkzeug.security import generate_password_hash, check_password_hash

APP_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", APP_DIR)).resolve()
AUTH_CONFIG_FILE = WORKSPACE_DIR / "auth_config.json"

# In-memory rate limiting: { ip: [list of failure timestamps] }
_FAILED_ATTEMPTS: Dict[str, List[float]] = {}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 900  # 15 minutes

# ==============================================================================
# 1. TOTP Multi-Factor Authentication (RFC 6238)
# ==============================================================================

def generate_totp_secret() -> str:
    """Generate a cryptographically secure 160-bit Base32 secret for TOTP."""
    return base64.b32encode(secrets.token_bytes(20)).decode("utf-8").replace("=", "").upper()

def get_totp_token(secret_b32: str, offset_intervals: int = 0, interval: int = 30) -> str:
    """Calculates current 6-digit TOTP token according to RFC 6238."""
    s = secret_b32.strip().upper()
    pad_len = (8 - len(s) % 8) % 8
    s += "=" * pad_len
    try:
        key = base64.b32decode(s, casefold=True)
    except Exception:
        return "000000"

    counter = (int(time.time()) // interval) + offset_intervals
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"

def verify_totp(secret_b32: str, token: str, window: int = 1, interval: int = 30) -> bool:
    """
    Verifies a 6-digit TOTP token against secret with +/- window interval tolerance
    to accommodate client-server clock drift.
    """
    if not secret_b32 or not token:
        return False
    token = token.strip().replace(" ", "")
    if not token.isdigit() or len(token) != 6:
        return False

    for offset in range(-window, window + 1):
        expected = get_totp_token(secret_b32, offset_intervals=offset, interval=interval)
        if hmac.compare_digest(expected, token):
            return True
    return False

def get_totp_uri(username: str, secret_b32: str, issuer: str = "JobHunt Command Center") -> str:
    """Builds otpauth:// URI for authenticator apps."""
    from urllib.parse import quote
    clean_user = quote(username.strip())
    clean_issuer = quote(issuer.strip())
    return f"otpauth://totp/{clean_issuer}:{clean_user}?secret={secret_b32}&issuer={clean_issuer}&algorithm=SHA1&digits=6&period=30"

def generate_qr_svg(otpauth_uri: str) -> Optional[str]:
    """Generates an inline SVG string for the TOTP setup QR code."""
    try:
        import qrcode
        import qrcode.image.svg
        import io
        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(otpauth_uri, image_factory=factory, box_size=10, border=2)
        stream = io.BytesIO()
        img.save(stream)
        svg_content = stream.getvalue().decode("utf-8")
        # Remove xml declaration if present for clean inline HTML embedding
        if "<?xml" in svg_content:
            svg_content = svg_content.split("?>", 1)[-1].strip()
        return svg_content
    except Exception as e:
        print(f"Notice: qrcode library not installed or error: {e}")
        return None

def generate_recovery_codes(count: int = 8) -> List[str]:
    """Generate emergency one-time recovery codes (e.g. A1B2-C3D4)."""
    codes = []
    for _ in range(count):
        part1 = secrets.token_hex(2).upper()
        part2 = secrets.token_hex(2).upper()
        codes.append(f"{part1}-{part2}")
    return codes

# ==============================================================================
# 2. Brute-Force Rate Limiting
# ==============================================================================

def check_rate_limit(ip: str) -> Tuple[bool, int]:
    """
    Checks whether an IP is currently locked out.
    Returns: (is_allowed: bool, remaining_lockout_seconds: int)
    """
    now = time.time()
    attempts = _FAILED_ATTEMPTS.get(ip, [])
    # Retain only attempts within the lockout window
    recent = [t for t in attempts if now - t < LOCKOUT_WINDOW_SECONDS]
    _FAILED_ATTEMPTS[ip] = recent

    if len(recent) >= MAX_FAILED_ATTEMPTS:
        remaining = int(LOCKOUT_WINDOW_SECONDS - (now - recent[0]))
        return False, max(1, remaining)
    return True, 0

def record_failed_attempt(ip: str) -> int:
    """Records a failed login attempt for an IP. Returns remaining attempts allowed."""
    now = time.time()
    attempts = _FAILED_ATTEMPTS.setdefault(ip, [])
    attempts.append(now)
    # prune old
    attempts[:] = [t for t in attempts if now - t < LOCKOUT_WINDOW_SECONDS]
    remaining = max(0, MAX_FAILED_ATTEMPTS - len(attempts))
    return remaining

def reset_rate_limit(ip: str) -> None:
    """Clears failed attempts for an IP after successful authentication."""
    _FAILED_ATTEMPTS.pop(ip, None)

# ==============================================================================
# 3. Cryptographic CSRF Tokens
# ==============================================================================

def generate_csrf_token(session_id: str, secret_key: str) -> str:
    """Generates a signed, timed CSRF token bound to the user session."""
    timestamp = str(int(time.time()))
    payload = f"{session_id}:{timestamp}".encode("utf-8")
    sig = hmac.new(secret_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{timestamp}:{sig}"

def validate_csrf_token(token: str, session_id: str, secret_key: str, max_age_seconds: int = 86400) -> bool:
    """Validates a CSRF token for freshness and authenticity."""
    if not token or ":" not in token:
        return False
    parts = token.split(":", 1)
    if len(parts) != 2:
        return False
    ts_str, provided_sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False

    now = int(time.time())
    if now - ts > max_age_seconds or ts > now + 300:
        return False  # Expired or from the future

    payload = f"{session_id}:{ts_str}".encode("utf-8")
    expected_sig = hmac.new(secret_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_sig, provided_sig)

# ==============================================================================
# 4. Credential & Auth Configuration Storage
# ==============================================================================

def load_auth_config() -> dict:
    """
    Loads authentication state from auth_config.json or environment variables.
    Environment variables take precedence.
    """
    config = {
        "auth_enabled": False,
        "username": "admin",
        "password_hash": "",
        "mfa_enabled": False,
        "totp_secret": "",
        "recovery_codes": [],
        "secret_key": ""
    }

    if AUTH_CONFIG_FILE.exists():
        try:
            stored = json.loads(AUTH_CONFIG_FILE.read_text(encoding="utf-8"))
            config.update(stored)
        except Exception as e:
            print(f"Warning: Could not parse {AUTH_CONFIG_FILE}: {e}")

    # Override / supplement with environment variables
    env_auth = os.environ.get("AUTH_ENABLED", "")
    if env_auth:
        config["auth_enabled"] = env_auth.lower() in ("true", "1", "yes", "on")

    env_user = os.environ.get("ADMIN_USERNAME", "")
    if env_user:
        config["username"] = env_user.strip()

    env_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")
    if env_hash:
        config["password_hash"] = env_hash.strip()
    else:
        env_pass = os.environ.get("ADMIN_PASSWORD", "")
        if env_pass:
            config["password_hash"] = generate_password_hash(env_pass.strip())
            if "auth_enabled" not in os.environ:
                config["auth_enabled"] = True

    env_mfa = os.environ.get("MFA_ENABLED", "")
    if env_mfa:
        config["mfa_enabled"] = env_mfa.lower() in ("true", "1", "yes", "on")

    env_secret = os.environ.get("SECRET_KEY", "")
    if env_secret:
        config["secret_key"] = env_secret.strip()
    elif not config.get("secret_key"):
        # Auto-generate a persistent secret key
        config["secret_key"] = secrets.token_hex(32)
        save_auth_config(config)

    # If password hash exists and auth_enabled was not explicitly set to False, default to enabled
    if config["password_hash"] and "AUTH_ENABLED" not in os.environ and not config.get("auth_enabled"):
        config["auth_enabled"] = True

    return config

def save_auth_config(config: dict) -> None:
    """Saves auth configuration locally (never committed to git)."""
    try:
        AUTH_CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
        os.chmod(AUTH_CONFIG_FILE, 0o600)  # Restricted read/write
    except Exception as e:
        print(f"Notice: Failed to save auth config: {e}")

def verify_credentials(username: str, password: str, config: dict) -> bool:
    """Checks username and password against hashed store."""
    if not username or not password:
        return False
    if not hmac.compare_digest(username.strip(), config.get("username", "admin")):
        return False
    p_hash = config.get("password_hash", "")
    if not p_hash:
        return False
    return check_password_hash(p_hash, password)

def consume_recovery_code(code: str, config: dict) -> bool:
    """Validates and burns a one-time emergency recovery code."""
    clean_code = code.strip().upper()
    existing_codes = config.get("recovery_codes", [])
    for idx, stored in enumerate(existing_codes):
        if hmac.compare_digest(stored.upper(), clean_code):
            existing_codes.pop(idx)
            config["recovery_codes"] = existing_codes
            save_auth_config(config)
            return True
    return False

# ==============================================================================
# CLI Management Utility
# ==============================================================================

if __name__ == "__main__":
    import getpass
    print("🔐 Job Hunt Command Center - Authentication Manager")
    config = load_auth_config()

    if len(sys.argv) > 1 and sys.argv[1] == "reset-mfa":
        config["mfa_enabled"] = False
        config["totp_secret"] = ""
        config["recovery_codes"] = []
        save_auth_config(config)
        print("✅ MFA has been reset and disabled.")
        sys.exit(0)

    print(f"Current status: AUTH_ENABLED={config.get('auth_enabled')}, MFA_ENABLED={config.get('mfa_enabled')}")
    user_input = input(f"Enter username [{config.get('username', 'admin')}]: ").strip() or config.get("username", "admin")
    pwd1 = getpass.getpass("Enter new password: ")
    pwd2 = getpass.getpass("Confirm new password: ")

    if pwd1 != pwd2:
        print("❌ Passwords do not match!")
        sys.exit(1)

    if len(pwd1) < 8:
        print("❌ Password must be at least 8 characters long.")
        sys.exit(1)

    config["username"] = user_input
    config["password_hash"] = generate_password_hash(pwd1)
    config["auth_enabled"] = True

    mfa_choice = input("Enable MFA (Time-based One-Time Password)? [Y/n]: ").strip().lower()
    if mfa_choice in ("", "y", "yes"):
        config["mfa_enabled"] = True
        secret = generate_totp_secret()
        config["totp_secret"] = secret
        config["recovery_codes"] = generate_recovery_codes(8)
        print("\n---------------------------------------------------------")
        print("🔑 MFA Secret Key (Base32):", secret)
        print("📲 Authenticator App URI:", get_totp_uri(user_input, secret))
        print("🚑 Emergency Recovery Codes (Save these in a secure place!):")
        for c in config["recovery_codes"]:
            print(f"   • {c}")
        print("---------------------------------------------------------\n")
    else:
        config["mfa_enabled"] = False

    save_auth_config(config)
    print("✅ Authentication configuration saved successfully!")
