"""Account-system tests: mailbox login, roles, api keys, localhost bypass."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.auth as auth
from api.marketing_app import create_marketing_app


@pytest.fixture
def client():
    return TestClient(create_marketing_app())


@pytest.fixture(autouse=True)
def imap_accepts_any(monkeypatch):
    """Tests run without a real corporate mail server: accept any credentials."""
    monkeypatch.setattr(auth, "imap_login_verify", lambda email, password: True)


@pytest.fixture
def imap_reject(monkeypatch):
    monkeypatch.setattr(auth, "imap_login_verify", lambda email, password: False)


class TestLogin:
    def test_login_creates_session(self, client, monkeypatch):
        auth.create_user("wendy@btrlgts.com", role="member", verify_password=None)
        res = client.post("/api/auth/login", json={"email": "Wendy@Btrlgts.com", "password": "secret"})
        assert res.status_code == 200
        assert res.json()["user"]["email"] == "wendy@btrlgts.com"
        assert "session" in res.cookies

    def test_login_rejected_credentials(self, client, imap_reject):
        res = client.post("/api/auth/login", json={"email": "wendy@btrlgts.com", "password": "bad"})
        assert res.status_code == 401

    def test_login_unreachable_mail_server_maps_to_503(self, client, monkeypatch):
        def boom(email, password):
            raise ConnectionError("cannot reach IMAP server")

        monkeypatch.setattr(auth, "imap_login_verify", boom)
        res = client.post("/api/auth/login", json={"email": "wendy@btrlgts.com", "password": "x"})
        assert res.status_code == 503

    def test_login_unregistered_mailbox_rejected(self, client, imap_accepts_any):
        res = client.post("/api/auth/login", json={"email": "stranger@btrlgts.com", "password": "x"})
        assert res.status_code == 401
        assert "not registered" in res.json()["detail"]

    def test_login_validates_input(self, client):
        assert client.post("/api/auth/login", json={"email": "not-an-email", "password": "x"}).status_code == 422
        assert client.post("/api/auth/login", json={"email": "a@b.co", "password": ""}).status_code == 422

    def test_me_without_session_reports_anonymous_local(self, client):
        # localhost callers pass the bypass; /me just reports who they are.
        res = client.get("/api/auth/me")
        assert res.status_code == 200
        assert res.json()["user"]["email"] == "local@localhost"

    def test_me_with_session(self, client):
        auth.create_user("wendy@btrlgts.com", role="admin", verify_password=None)
        client.post("/api/auth/login", json={"email": "wendy@btrlgts.com", "password": "x"})
        res = client.get("/api/auth/me")
        assert res.status_code == 200
        assert res.json()["user"]["role"] == "admin"

    def test_logout_clears_session(self, client):
        auth.create_user("wendy@btrlgts.com", verify_password=None)
        client.post("/api/auth/login", json={"email": "wendy@btrlgts.com", "password": "x"})
        assert client.get("/api/auth/me").json()["user"]["email"] == "wendy@btrlgts.com"
        assert client.post("/api/auth/logout").status_code == 200
        # Session gone; localhost bypass answers again with the anonymous identity.
        assert client.get("/api/auth/me").json()["user"]["email"] == "local@localhost"


class TestRoles:
    def test_member_cannot_read_settings(self, client):
        auth.create_user("member@btrlgts.com", role="member", verify_password=None)
        client.post("/api/auth/login", json={"email": "member@btrlgts.com", "password": "x"})
        assert client.get("/api/settings").status_code == 403
        assert client.get("/api/auth/users").status_code == 403

    def test_admin_can_read_settings(self, client):
        auth.create_user("admin@btrlgts.com", role="admin", verify_password=None)
        client.post("/api/auth/login", json={"email": "admin@btrlgts.com", "password": "x"})
        assert client.get("/api/settings").status_code == 200

    def test_member_operational_surface_allowed(self, client):
        auth.create_user("member@btrlgts.com", role="member", verify_password=None)
        client.post("/api/auth/login", json={"email": "member@btrlgts.com", "password": "x"})
        assert client.get("/api/v1/email-drafts?status=draft").status_code == 200
        assert client.get("/api/v1/email-drafts?status=approved").status_code == 200


class TestApiKeys:
    def test_user_api_key_authenticates_with_role(self, client):
        user = auth.create_user("ops@btrlgts.com", role="member", verify_password=None)

        ok = client.get("/api/v1/email-drafts", headers={"X-API-Key": user["api_key"]})
        assert ok.status_code == 200

        forbidden = client.get("/api/settings", headers={"X-API-Key": user["api_key"]})
        assert forbidden.status_code == 403

    def test_reset_key_invalidates_old(self, client):
        user = auth.create_user("ops2@btrlgts.com", verify_password=None)
        old = user["api_key"]
        rotated = auth.reset_api_key("ops2@btrlgts.com")

        assert client.get("/api/v1/email-drafts", headers={"X-API-Key": old}).status_code == 401
        assert client.get("/api/v1/email-drafts", headers={"X-API-Key": rotated["api_key"]}).status_code == 200

    def test_inactive_user_key_rejected(self, client):
        user = auth.create_user("gone@btrlgts.com", verify_password=None)
        with auth.get_session() as session:
            from persistence.db import execute

            execute(session, "UPDATE users SET active = false WHERE email = ?", ("gone@btrlgts.com",))
        assert client.get("/api/v1/email-drafts", headers={"X-API-Key": user["api_key"]}).status_code == 401


class TestLegacyTokenAndLocalhost:
    def test_legacy_shared_token_still_grants_admin(self, monkeypatch):
        """The break-glass token keeps working: admin role, wrong value 401s."""
        import config.settings as cs

        monkeypatch.setenv("API_ACCESS_TOKEN", "legacy-secret")
        cs.get_settings.cache_clear()
        try:
            client = TestClient(create_marketing_app())
            ok = client.get("/api/settings", headers={"X-API-Key": "legacy-secret"})
            assert ok.status_code == 200
            wrong = client.get("/api/settings", headers={"X-API-Key": "not-the-token"})
            assert wrong.status_code == 401
        finally:
            cs.get_settings.cache_clear()

    def test_user_management_admin_only(self, client):
        auth.create_user("admin@btrlgts.com", role="admin", verify_password=None)
        client.post("/api/auth/login", json={"email": "admin@btrlgts.com", "password": "x"})
        res = client.post("/api/auth/users", json={"email": "new@btrlgts.com", "password": "x"})
        assert res.status_code == 200
        emails = [u["email"] for u in client.get("/api/auth/users").json()]
        assert "new@btrlgts.com" in emails

    def test_localhost_bypass_still_works(self, client):
        # TestClient host is whitelisted; no credentials at all.
        assert client.get("/api/v1/email-drafts?status=draft").status_code == 200


class TestReviewPageAuthGate:
    def test_review_redirects_anonymous_to_login(self, client):
        res = client.get("/review", follow_redirects=False)
        assert res.status_code == 302
        assert "/login" in res.headers["location"]

    def test_review_serves_page_after_login(self, client):
        auth.create_user("wendy@btrlgts.com", verify_password=None)
        client.post("/api/auth/login", json={"email": "wendy@btrlgts.com", "password": "x"})
        res = client.get("/review", follow_redirects=False)
        assert res.status_code == 200

    def test_login_page_served_openly(self, client):
        assert client.get("/login").status_code == 200
