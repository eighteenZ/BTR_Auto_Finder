"""API test shared fixtures.

The corporate-mailbox IMAP verification must not hit a real mail server in
tests: accept any credentials. (Only api.auth uses imap_login_verify.)
"""

import pytest

import api.auth as auth


@pytest.fixture(autouse=True)
def imap_accepts_any(monkeypatch):
    monkeypatch.setattr(auth, "imap_login_verify", lambda email, password: True)
