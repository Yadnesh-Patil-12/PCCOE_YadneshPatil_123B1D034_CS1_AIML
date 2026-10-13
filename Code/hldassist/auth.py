"""Demo-grade authentication and role based access control (PBKDF2 hashes, in-memory sessions)."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

ROLES = {
    "viewer": {"ask", "read"},
    "tester": {"ask", "read", "export", "rate"},
    "architect": {"ask", "read", "export", "rate", "ingest", "review", "run_checks", "audit"},
    "admin": {"ask", "read", "export", "rate", "ingest", "review", "run_checks", "audit"},
}

DEMO_USERS = [
    # username, display, role, password, projects
    ("alice", "Alice Rao (Architect)", "architect", "demo123", ["PRJ-BODY", "PRJ-ENERGY"]),
    ("bob", "Bob Patil (Test engineer)", "tester", "demo123", ["PRJ-BODY"]),
    ("carol", "Carol Shah (Viewer)", "viewer", "demo123", ["PRJ-ENERGY"]),
]


def hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000).hex()


def seed_users(db) -> None:
    if db.one("SELECT 1 AS x FROM users LIMIT 1"):
        return
    for u, disp, role, pw, projects in DEMO_USERS:
        salt = secrets.token_hex(16)
        db.run("INSERT INTO users(username,display,role,salt,pw_hash,projects) VALUES(?,?,?,?,?,?)",
               (u, disp, role, salt, hash_pw(pw, salt), json.dumps(projects)))


class Sessions:
    def __init__(self, ttl_minutes: int):
        self.ttl = ttl_minutes * 60
        self.data: dict[str, dict] = {}

    def login(self, db, username: str, password: str):
        row = db.one("SELECT * FROM users WHERE username=?", (username,))
        if not row or not hmac.compare_digest(hash_pw(password, row["salt"]), row["pw_hash"]):
            return None
        tok = secrets.token_urlsafe(32)
        self.data[tok] = dict(username=row["username"], display=row["display"], role=row["role"],
                              projects=json.loads(row["projects"]), exp=time.time() + self.ttl)
        return tok

    def get(self, token: str | None):
        s = self.data.get(token or "")
        if not s:
            return None
        if s["exp"] < time.time():
            self.data.pop(token, None)
            return None
        return s

    def logout(self, token: str) -> None:
        self.data.pop(token, None)


def allowed(user: dict, perm: str) -> bool:
    return perm in ROLES.get(user["role"], set())
