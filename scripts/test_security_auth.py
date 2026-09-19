#!/usr/bin/env python3
import os
import sys
import unittest
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from scripts.auth_manager import (
    generate_totp_secret, get_totp_token, verify_totp,
    generate_recovery_codes, consume_recovery_code,
    check_rate_limit, record_failed_attempt, reset_rate_limit,
    generate_csrf_token, validate_csrf_token,
    verify_credentials, load_auth_config
)
from werkzeug.security import generate_password_hash

class TestSecurityEngine(unittest.TestCase):

    def test_totp_rfc6238(self):
        secret = generate_totp_secret()
        self.assertEqual(len(secret), 32)
        
        token = get_totp_token(secret)
        self.assertEqual(len(token), 6)
        self.assertTrue(token.isdigit())
        
        # Verify valid token
        self.assertTrue(verify_totp(secret, token))
        
        # Verify rejection of invalid tokens
        self.assertFalse(verify_totp(secret, "000000" if token != "000000" else "111111"))
        self.assertFalse(verify_totp(secret, "abc"))
        self.assertFalse(verify_totp(secret, ""))
        
        # Verify clock drift tolerance (-1, 0, +1 intervals)
        token_prev = get_totp_token(secret, offset_intervals=-1)
        self.assertTrue(verify_totp(secret, token_prev, window=1))
        
        # Token from 5 intervals ago must fail with window=1
        token_past = get_totp_token(secret, offset_intervals=-5)
        self.assertFalse(verify_totp(secret, token_past, window=1))

    def test_recovery_codes(self):
        codes = generate_recovery_codes(4)
        self.assertEqual(len(codes), 4)
        cfg = {"recovery_codes": list(codes)}
        
        # Burn first code
        code_to_burn = codes[0]
        self.assertTrue(consume_recovery_code(code_to_burn, cfg))
        self.assertEqual(len(cfg["recovery_codes"]), 3)
        
        # Cannot burn same code twice
        self.assertFalse(consume_recovery_code(code_to_burn, cfg))
        
        # Invalid code fails
        self.assertFalse(consume_recovery_code("INVALID-CODE", cfg))

    def test_rate_limiting(self):
        ip = "192.0.2.42"
        reset_rate_limit(ip)
        
        # First 4 attempts should be allowed
        for i in range(4):
            allowed, _ = check_rate_limit(ip)
            self.assertTrue(allowed)
            remaining = record_failed_attempt(ip)
            self.assertEqual(remaining, 4 - i)
            
        # 5th attempt recorded
        record_failed_attempt(ip)
        
        # Now 6th attempt must be locked out
        allowed, wait_sec = check_rate_limit(ip)
        self.assertFalse(allowed)
        self.assertGreater(wait_sec, 0)
        
        # Reset allows again
        reset_rate_limit(ip)
        allowed, _ = check_rate_limit(ip)
        self.assertTrue(allowed)

    def test_csrf_tokens(self):
        session_id = "sess_abc123"
        secret = "super_secret_test_key_32bytes_long"
        
        token = generate_csrf_token(session_id, secret)
        self.assertIn(":", token)
        
        # Valid token
        self.assertTrue(validate_csrf_token(token, session_id, secret))
        
        # Invalid session
        self.assertFalse(validate_csrf_token(token, "different_session", secret))
        
        # Invalid secret
        self.assertFalse(validate_csrf_token(token, session_id, "wrong_secret"))
        
        # Tampered token
        tampered = token + "xyz"
        self.assertFalse(validate_csrf_token(tampered, session_id, secret))

    def test_password_verification(self):
        pwd = "TestStrongPassword123!"
        p_hash = generate_password_hash(pwd)
        cfg = {"username": "secops", "password_hash": p_hash}
        
        self.assertTrue(verify_credentials("secops", pwd, cfg))
        self.assertFalse(verify_credentials("secops", "WrongPassword", cfg))
        self.assertFalse(verify_credentials("wronguser", pwd, cfg))

class TestDashboardEndpointsWithAuth(unittest.TestCase):

    def setUp(self):
        os.environ["AUTH_ENABLED"] = "true"
        os.environ["ADMIN_USERNAME"] = "testadmin"
        os.environ["ADMIN_PASSWORD"] = "SecretPass123!"
        os.environ["MFA_ENABLED"] = "false"
        os.environ["SECRET_KEY"] = "test_flask_secret_key_1234567890"

        from dashboard.app import app
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

    def tearDown(self):
        os.environ.pop("AUTH_ENABLED", None)
        os.environ.pop("ADMIN_USERNAME", None)
        os.environ.pop("ADMIN_PASSWORD", None)
        os.environ.pop("MFA_ENABLED", None)
        os.environ.pop("SECRET_KEY", None)

    def test_public_healthz(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "healthy")

    def test_unauthenticated_redirection(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.headers["Location"])

    def test_unauthenticated_api_rejection(self):
        res = self.client.get("/api/jobs")
        self.assertEqual(res.status_code, 401)
        data = res.get_json()
        self.assertIn("error", data)

    def test_security_headers_present(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("Content-Security-Policy", res.headers)
        self.assertEqual(res.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")

    def test_login_flow(self):
        # 1. Invalid login
        res = self.client.post("/login", data={"username": "testadmin", "password": "wrong"})
        self.assertEqual(res.status_code, 401)

        # 2. Valid login
        res = self.client.post("/login", data={"username": "testadmin", "password": "SecretPass123!"}, follow_redirects=False)
        self.assertEqual(res.status_code, 302)

        # 3. Access dashboard with established session
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Job Hunt Command Center", res.data)
        self.assertIn(b"testadmin", res.data)

        # 4. Access API with established session
        res = self.client.get("/api/jobs")
        self.assertEqual(res.status_code, 200)

if __name__ == "__main__":
    unittest.main()
