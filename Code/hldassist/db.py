"""SQLite persistence: users, documents, chunks, findings, saved answers, audit trail."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, display TEXT, role TEXT, salt TEXT, pw_hash TEXT, projects TEXT);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, doc_id TEXT, version TEXT, title TEXT,
  family TEXT, status TEXT, path TEXT, pages INTEGER, chunks INTEGER, sha256 TEXT, ingested_at REAL, uploaded_by TEXT,
  UNIQUE(project, doc_id, version));
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, doc_id TEXT, version TEXT, page INTEGER,
  sec_id TEXT, sec_title TEXT, text TEXT, injection INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS findings(id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, fingerprint TEXT, rule TEXT, severity TEXT,
  title TEXT, detail TEXT, evidence TEXT, status TEXT DEFAULT 'open', reviewer TEXT, note TEXT, updated_at REAL, UNIQUE(project, fingerprint));
CREATE TABLE IF NOT EXISTS answers(id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, username TEXT, question TEXT, answer TEXT,
  citations TEXT, confidence REAL, label TEXT, refused INTEGER, review_status TEXT DEFAULT 'pending', edited TEXT, reviewer TEXT,
  latency_ms INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, username TEXT, action TEXT, project TEXT, detail TEXT);
"""


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def q(self, sql, args=()):
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def run(self, sql, args=()):
        with self.conn() as c:
            return c.execute(sql, args).lastrowid

    def audit(self, user, action, project=None, detail=""):
        self.run("INSERT INTO audit(ts,username,action,project,detail) VALUES(?,?,?,?,?)",
                 (time.time(), user, action, project, detail[:500]))

    # ---- convenience -------------------------------------------------- #
    def upsert_findings(self, project: str, findings: list[dict]) -> None:
        now = time.time()
        with self.conn() as c:
            current = {f["fingerprint"] for f in findings}
            for f in findings:
                row = c.execute("SELECT id,status FROM findings WHERE project=? AND fingerprint=?",
                                (project, f["fingerprint"])).fetchone()
                ev = json.dumps(f["evidence"])
                if row:
                    status = row["status"] if row["status"] != "resolved" else "open"
                    c.execute("UPDATE findings SET rule=?,severity=?,title=?,detail=?,evidence=?,status=? WHERE id=?",
                              (f["rule"], f["severity"], f["title"], f["detail"], ev, status, row["id"]))
                else:
                    c.execute("INSERT INTO findings(project,fingerprint,rule,severity,title,detail,evidence,status,updated_at)"
                              " VALUES(?,?,?,?,?,?,?, 'open', ?)",
                              (project, f["fingerprint"], f["rule"], f["severity"], f["title"], f["detail"], ev, now))
            # findings that no longer occur are auto-resolved (keeps the audit history)
            for r in c.execute("SELECT id,fingerprint,status FROM findings WHERE project=?", (project,)).fetchall():
                if r["fingerprint"] not in current and r["status"] != "resolved":
                    c.execute("UPDATE findings SET status='resolved', updated_at=? WHERE id=?", (now, r["id"]))
