"""HTTP API + static file server built on the Python standard library (no framework required)."""
from __future__ import annotations

import json
import mimetypes
import re
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config as C
from .auth import Sessions, allowed
from .service import Service

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def make_handler(svc: Service, sessions: Sessions):
    cfg = svc.cfg
    max_bytes = cfg["server"]["max_upload_mb"] * 1024 * 1024

    class Handler(BaseHTTPRequestHandler):
        server_version = "HLDAssistant/1.0"

        def log_message(self, fmt, *args):  # concise access log
            if "/api/" in (args[0] if args else ""):
                print(f"[{time.strftime('%H:%M:%S')}] {args[0]} -> {args[1]}")

        # ------------------------------------------------------------ helpers
        def _send(self, status, body: bytes, ctype="application/json", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj, default=str).encode())

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > max_bytes:
                raise ApiError(413, f"File too large (limit {cfg['server']['max_upload_mb']} MB)")
            return self.rfile.read(n) if n else b""

        def _payload(self) -> dict:
            raw = self._body()
            try:
                return json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                raise ApiError(400, "Request body must be valid JSON")

        def _user(self):
            h = self.headers.get("Authorization", "")
            tok = h[7:] if h.startswith("Bearer ") else None
            u = sessions.get(tok)
            if not u:
                raise ApiError(401, "Please sign in")
            u["token"] = tok
            return u

        def _need(self, user, perm):
            if not allowed(user, perm):
                raise ApiError(403, f"Your role ({user['role']}) is not allowed to do this")

        def _project(self, user, pid):
            if pid not in user["projects"]:
                svc.db.audit(user["username"], "denied_project", pid, "access to project refused")
                raise ApiError(403, f"You do not have access to project {pid}")
            return pid

        # ------------------------------------------------------------ dispatch
        def do_GET(self): self._dispatch("GET")
        def do_POST(self): self._dispatch("POST")
        def do_DELETE(self): self._dispatch("DELETE")

        def _dispatch(self, method):
            url = urlparse(self.path)
            path = url.path
            try:
                if path.startswith("/api/"):
                    self._api(method, path, parse_qs(url.query))
                elif method == "GET":
                    self._static(path)
                else:
                    raise ApiError(405, "Method not allowed")
            except ApiError as e:
                self._json({"error": e.message}, e.status)
            except KeyError as e:
                self._json({"error": f"Not found: {e.args[0]}"}, 404)
            except ValueError as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:  # never leak a stack trace to the browser
                print("ERROR", repr(e))
                self._json({"error": "Internal error. See the server console."}, 500)

        def _static(self, path):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (C.WEB_DIR / rel).resolve()
            if C.WEB_DIR.resolve() not in target.parents and target != C.WEB_DIR.resolve():
                raise ApiError(404, "Not found")
            if not target.is_file():
                raise ApiError(404, "Not found")
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") or "javascript" in ctype else ""))

        # ------------------------------------------------------------ API
        def _api(self, method, path, qs):
            seg = [s for s in path.split("/") if s][1:]  # drop "api"
            q1 = lambda k, d="": (qs.get(k) or [d])[0]

            if seg == ["health"] and method == "GET":
                return self._json(dict(status="ok", version="1.0.0", **svc.system_info()))
            if seg == ["login"] and method == "POST":
                b = self._payload()
                tok = sessions.login(svc.db, str(b.get("username", "")).strip().lower(), str(b.get("password", "")))
                if not tok:
                    svc.db.audit(str(b.get("username", ""))[:40], "login_failed")
                    raise ApiError(401, "Wrong user name or password")
                u = sessions.get(tok)
                svc.db.audit(u["username"], "login")
                return self._json(dict(token=tok, user=dict(username=u["username"], display=u["display"], role=u["role"], projects=u["projects"])))
            if seg == ["logout"] and method == "POST":
                u = self._user(); sessions.logout(u["token"]); return self._json({"ok": True})

            user = self._user()
            if seg == ["me"]:
                return self._json(dict(username=user["username"], display=user["display"], role=user["role"], projects=user["projects"]))
            if seg == ["projects"]:
                rows = svc.db.q("SELECT id,name FROM projects")
                return self._json([r for r in rows if r["id"] in user["projects"]])
            if seg == ["evaluation"]:
                f = C.EVAL_DIR / "evaluation_results.json"
                if not f.exists():
                    raise ApiError(404, "No evaluation has been run yet. Run: python scripts/run_evaluation.py")
                data = json.loads(f.read_text(encoding="utf-8"))
                rows = []
                csvf = C.EVAL_DIR / "qa_results.csv"
                if csvf.exists():
                    import csv
                    rows = list(csv.DictReader(open(csvf, encoding="utf-8")))
                return self._json(dict(summary=data, rows=rows))
            if seg == ["audit"]:
                self._need(user, "audit")
                rows = svc.db.q("SELECT * FROM audit WHERE project IS NULL OR project IN (%s) ORDER BY id DESC LIMIT 200" %
                                ",".join("?" * len(user["projects"])), tuple(user["projects"]))
                return self._json(rows)
            if len(seg) == 3 and seg[0] == "findings" and seg[2] == "review" and method == "POST":
                self._need(user, "review")
                fid = int(seg[1])
                row = svc.db.one("SELECT project FROM findings WHERE id=?", (fid,))
                if not row:
                    raise KeyError("finding")
                self._project(user, row["project"])
                b = self._payload()
                svc.review_finding(fid, b.get("status", ""), str(b.get("note", "")), user["username"])
                return self._json({"ok": True})
            if len(seg) == 3 and seg[0] == "answers" and seg[2] == "review" and method == "POST":
                self._need(user, "rate")
                aid = int(seg[1])
                row = svc.db.one("SELECT project FROM answers WHERE id=?", (aid,))
                if not row:
                    raise KeyError("answer")
                self._project(user, row["project"])
                b = self._payload()
                if b.get("status") in ("accepted", "rejected", "edited") and not allowed(user, "review"):
                    raise ApiError(403, "Only an architect can approve or reject answers")
                svc.review_answer(aid, b.get("status", ""), str(b.get("edited", "")), user["username"])
                return self._json({"ok": True})

            if len(seg) >= 3 and seg[0] == "projects":
                pid = self._project(user, seg[1])
                rest = seg[2:]
                if rest == ["stats"]: return self._json(svc.stats(pid))
                if rest == ["documents"] and method == "GET": return self._json(svc.documents(pid))
                if rest == ["documents"] and method == "POST":
                    self._need(user, "ingest")
                    name = Path(q1("filename", "upload.pdf")).name
                    suffix = Path(name).suffix.lower()
                    data = self._body()
                    if not data:
                        raise ApiError(400, "Empty file")
                    if suffix == ".pdf" and not data.startswith(b"%PDF"):
                        raise ApiError(400, "This file is not a valid PDF")
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tf:
                        tf.write(data)
                    try:
                        doc_id = q1("doc_id").strip() or re.sub(r"[^A-Za-z0-9._\-]", "-", Path(name).stem)[:40]
                        res = svc.ingest(pid, Path(tf.name), doc_id, q1("version", "1.0").strip(), q1("title").strip() or doc_id,
                                         user=user["username"])
                    finally:
                        Path(tf.name).unlink(missing_ok=True)
                    return self._json(res, 201)
                if len(rest) == 3 and rest[0] == "documents" and method == "DELETE":
                    self._need(user, "ingest")
                    svc.delete_document(pid, rest[1], rest[2], user["username"])
                    return self._json({"ok": True})
                if rest == ["ask"] and method == "POST":
                    self._need(user, "ask")
                    b = self._payload()
                    qn = str(b.get("question", "")).strip()
                    if len(qn) < 3 or len(qn) > 600:
                        raise ApiError(400, "Ask a question between 3 and 600 characters")
                    return self._json(svc.ask(pid, qn, user["username"]))
                if rest == ["inventory"]: return self._json(svc.inventory(pid, q1("kind", "components")))
                if rest == ["export"]:
                    self._need(user, "export")
                    body, ctype = svc.export(pid, q1("kind", "components"), q1("format", "csv"))
                    svc.db.audit(user["username"], "export", pid, f"{q1('kind','components')} {q1('format','csv')}")
                    return self._send(200, body.encode(), ctype, {"Content-Disposition": f'attachment; filename="{pid}_{q1("kind","components")}.{q1("format","csv")}"'})
                if rest == ["graph"]: return self._json(svc.graph(pid))
                if rest == ["impact"]: return self._json(svc.impact(pid, q1("entity")))
                if rest == ["findings"] and method == "GET": return self._json(svc.findings(pid))
                if rest == ["findings", "run"] and method == "POST":
                    self._need(user, "run_checks")
                    n = len(svc.run_checks(pid, user["username"]))
                    return self._json(dict(count=n, findings=svc.findings(pid)))
                if rest == ["compare"]: return self._json(svc.compare(pid, q1("a"), q1("b")))
                if rest == ["answers"]: return self._json(svc.answers(pid))
            raise ApiError(404, "Unknown endpoint")

    return Handler


def serve(host=None, port=None):
    svc = Service()
    cfg = svc.cfg
    host = host or cfg["server"]["host"]
    port = int(port or cfg["server"]["port"])
    sessions = Sessions(cfg["server"]["session_ttl_minutes"])
    httpd = ThreadingHTTPServer((host, port), make_handler(svc, sessions))
    info = svc.system_info()
    print("=" * 64)
    print(" HLD Assistant is running")
    print(f" Open:   http://{host}:{port}")
    print(f" Models: embeddings={info['embedding']}, vector store={info['vector_store']}, LLM={info['llm']}")
    print(" Demo logins (password demo123): alice (architect), bob (tester), carol (viewer)")
    print(" Stop with Ctrl+C")
    print("=" * 64)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBye")
    finally:
        httpd.server_close()
