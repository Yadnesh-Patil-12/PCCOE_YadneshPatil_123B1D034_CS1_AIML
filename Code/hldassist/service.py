"""Application service: ingestion, per-project indexes, checks, comparison, export. UI-agnostic."""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import threading
import time
from pathlib import Path

from . import config as C
from .auth import seed_users
from .checks import run_checks
from .compare import compare_inventories, compare_sections, summarize
from .db import DB
from .extract import extract_document, merge_project
from .graph import ArchGraph
from .ingest import file_sha256, ingest_file
from .qa import QAEngine
from .retrieval import HybridIndex

PROJECT_NAMES = {"PRJ-BODY": "Body Platform (BCM)", "PRJ-ENERGY": "Energy Platform (BMS)"}
ALLOWED_EXT = {".pdf", ".md", ".txt"}


class Service:
    def __init__(self, cfg: dict | None = None, data_dir: Path | None = None):
        self.cfg = cfg or C.load_config()
        self.prompts = C.load_prompts()
        self.data_dir = Path(data_dir or C.DATA_DIR)
        self.db = DB(self.data_dir / "hld_assistant.sqlite")
        self.upload_dir = self.data_dir / "uploads"
        self.lock = threading.RLock()
        self._parsed: dict[tuple, tuple] = {}      # (project, doc_id, version) -> (sections, extraction)
        self._state: dict[str, dict] = {}          # project -> index state
        seed_users(self.db)
        self._seed()

    # ---------------------------------------------------------------- #
    def _seed(self) -> None:
        manifest = C.INPUT_DIR / "manifest.json"
        for pid, name in PROJECT_NAMES.items():
            if not self.db.one("SELECT 1 AS x FROM projects WHERE id=?", (pid,)):
                self.db.run("INSERT INTO projects(id,name) VALUES(?,?)", (pid, name))
        if self.db.one("SELECT 1 AS x FROM documents LIMIT 1") or not manifest.exists():
            return
        for m in json.loads(manifest.read_text(encoding="utf-8")):
            self.ingest(m["project"], C.INPUT_DIR / m["file"], m["doc_id"], m["version"], m["title"],
                        status=m["status"], user="system", copy=False, rebuild=False)
        self.run_checks("PRJ-BODY", "system")
        self.run_checks("PRJ-ENERGY", "system")

    # ---------------------------------------------------------------- #
    def ingest(self, project, path: Path, doc_id, version, title, status="approved", user="system", copy=True, rebuild=True):
        path = Path(path)
        if path.suffix.lower() not in ALLOWED_EXT:
            raise ValueError("Unsupported file type. Use PDF, MD or TXT.")
        if project not in PROJECT_NAMES and not self.db.one("SELECT 1 AS x FROM projects WHERE id=?", (project,)):
            raise ValueError("Unknown project")
        if not re.fullmatch(r"[A-Za-z0-9._\-]{1,40}", doc_id or "") or not re.fullmatch(r"[A-Za-z0-9._\-]{1,20}", version or ""):
            raise ValueError("Document ID and version may only contain letters, digits, dot, dash and underscore.")
        with self.lock:
            if copy:
                dest = self.upload_dir / project / f"{doc_id}_v{version}{path.suffix.lower()}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                if path.resolve() != dest.resolve():
                    shutil.copyfile(path, dest)
                path = dest
            sections, chunks, pages = ingest_file(path, title, self.cfg, self.prompts["injection_patterns"])
            self.db.run("DELETE FROM chunks WHERE project=? AND doc_id=? AND version=?", (project, doc_id, version))
            self.db.run("DELETE FROM documents WHERE project=? AND doc_id=? AND version=?", (project, doc_id, version))
            family = doc_id
            if status == "approved":
                self.db.run("UPDATE documents SET status='superseded' WHERE project=? AND family=? AND status='approved'",
                            (project, family))
            with self.db.conn() as c:
                for ch in chunks:
                    c.execute("INSERT INTO chunks(project,doc_id,version,page,sec_id,sec_title,text,injection) VALUES(?,?,?,?,?,?,?,?)",
                              (project, doc_id, version, ch.page, ch.sec_id, ch.sec_title, ch.text, int(getattr(ch, "injection", False))))
                try:
                    rel = str(path.relative_to(C.PROJECT_DIR))
                except ValueError:
                    rel = str(path)
                c.execute("INSERT INTO documents(project,doc_id,version,title,family,status,path,pages,chunks,sha256,ingested_at,uploaded_by)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (project, doc_id, version, title, family, status, rel, pages, len(chunks), file_sha256(path), time.time(), user))
            self._parsed[(project, doc_id, version)] = (sections, extract_document(
                dict(doc_id=doc_id, version=version, title=title), sections))
            self._state.pop(project, None)
            self.db.audit(user, "ingest", project, f"{doc_id} v{version} ({pages} pages, {len(chunks)} chunks)")
        if rebuild:
            self.run_checks(project, user)
        return dict(doc_id=doc_id, version=version, pages=pages, chunks=len(chunks))

    def delete_document(self, project, doc_id, version, user):
        with self.lock:
            self.db.run("DELETE FROM chunks WHERE project=? AND doc_id=? AND version=?", (project, doc_id, version))
            self.db.run("DELETE FROM documents WHERE project=? AND doc_id=? AND version=?", (project, doc_id, version))
            self._parsed.pop((project, doc_id, version), None)
            # if the approved revision was removed, the newest remaining revision becomes the approved one again
            if not self.db.one("SELECT 1 AS x FROM documents WHERE project=? AND family=? AND status='approved'", (project, doc_id)):
                last = self.db.one("SELECT id FROM documents WHERE project=? AND family=? ORDER BY ingested_at DESC", (project, doc_id))
                if last:
                    self.db.run("UPDATE documents SET status='approved' WHERE id=?", (last["id"],))
            self._state.pop(project, None)
            self.db.audit(user, "delete_document", project, f"{doc_id} v{version}")
        self.run_checks(project, user)

    # ---------------------------------------------------------------- #
    def documents(self, project):
        return self.db.q("SELECT doc_id,version,title,status,pages,chunks,sha256,ingested_at,uploaded_by FROM documents "
                         "WHERE project=? ORDER BY doc_id, version", (project,))

    def _parsed_doc(self, project, row):
        key = (project, row["doc_id"], row["version"])
        if key not in self._parsed:
            path = Path(row["path"])
            path = path if path.is_absolute() else C.PROJECT_DIR / path
            sections, _, _ = ingest_file(path, row["title"], self.cfg, self.prompts["injection_patterns"])
            self._parsed[key] = (sections, extract_document(dict(doc_id=row["doc_id"], version=row["version"], title=row["title"]), sections))
        return self._parsed[key]

    def _project_state(self, project) -> dict:
        with self.lock:
            if project in self._state:
                return self._state[project]
            rows = self.db.q("SELECT * FROM documents WHERE project=? AND status='approved'", (project,))
            titles = {(r["doc_id"], r["version"]): r["title"] for r in rows}
            chunks, suspicious = [], []
            for r in self.db.q("SELECT c.* FROM chunks c JOIN documents d ON d.project=c.project AND d.doc_id=c.doc_id AND d.version=c.version "
                               "WHERE c.project=? AND d.status='approved' ORDER BY c.id", (project,)):
                r["doc_title"] = titles.get((r["doc_id"], r["version"]), "")
                if r["injection"]:
                    suspicious.append(dict(doc_id=r["doc_id"], version=r["version"], title=r["doc_title"], page=r["page"],
                                           sec_id=r["sec_id"], sec_title=r["sec_title"]))
                else:
                    chunks.append(r)
            inv = merge_project([self._parsed_doc(project, r)[1] for r in rows])
            graph = ArchGraph(inv)
            index = HybridIndex(self.cfg, chunks)
            qa = QAEngine(self.cfg, self.prompts, index, inv, graph, project, rows)
            st = dict(inv=inv, graph=graph, index=index, qa=qa, suspicious=suspicious, docs=rows)
            self._state[project] = st
            return st

    # ---------------------------------------------------------------- #
    def ask(self, project, question, user):
        res = self._project_state(project)["qa"].ask(question)
        aid = self.db.run("INSERT INTO answers(project,username,question,answer,citations,confidence,label,refused,latency_ms,ts)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (project, user, question, res["answer"], json.dumps(res["citations"]), res["confidence"], res["label"],
                           int(res["refused"]), res["latency_ms"], time.time()))
        res["id"] = aid
        self.db.audit(user, "ask", project, question[:200])
        return res

    def inventory(self, project, kind):
        inv = self._project_state(project)["inv"]
        if kind not in inv:
            raise ValueError("kind must be components, ports, interfaces or signals")
        return inv[kind]

    def graph(self, project):
        return self._project_state(project)["graph"].to_json()

    def impact(self, project, entity):
        return self._project_state(project)["graph"].impact(entity)

    def run_checks(self, project, user):
        st = self._project_state(project)
        findings = run_checks(st["inv"], st["suspicious"])
        self.db.upsert_findings(project, findings)
        self.db.audit(user, "run_checks", project, f"{len(findings)} findings")
        return findings

    def findings(self, project):
        rows = self.db.q("SELECT * FROM findings WHERE project=? ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id", (project,))
        for r in rows:
            r["evidence"] = json.loads(r["evidence"])
        return rows

    def review_finding(self, fid, status, note, user):
        if status not in ("open", "accepted", "rejected", "resolved"):
            raise ValueError("Invalid status")
        row = self.db.one("SELECT project FROM findings WHERE id=?", (fid,))
        if not row:
            raise KeyError("finding")
        self.db.run("UPDATE findings SET status=?, note=?, reviewer=?, updated_at=? WHERE id=?", (status, note[:1000], user, time.time(), fid))
        self.db.audit(user, f"finding_{status}", row["project"], f"#{fid} {note[:120]}")
        return row["project"]

    def review_answer(self, aid, status, edited, user):
        if status not in ("pending", "accepted", "rejected", "edited"):
            raise ValueError("Invalid status")
        row = self.db.one("SELECT project FROM answers WHERE id=?", (aid,))
        if not row:
            raise KeyError("answer")
        self.db.run("UPDATE answers SET review_status=?, edited=?, reviewer=? WHERE id=?", (status, (edited or "")[:4000], user, aid))
        self.db.audit(user, f"answer_{status}", row["project"], f"#{aid}")
        return row["project"]

    def answers(self, project):
        rows = self.db.q("SELECT * FROM answers WHERE project=? ORDER BY id DESC LIMIT 100", (project,))
        for r in rows:
            r["citations"] = json.loads(r["citations"])
        return rows

    def compare(self, project, a, b):
        """a, b: 'DOC_ID:version'"""
        out = []
        for ref in (a, b):
            doc_id, _, ver = ref.partition(":")
            row = self.db.one("SELECT * FROM documents WHERE project=? AND doc_id=? AND version=?", (project, doc_id, ver))
            if not row:
                raise KeyError(f"Document {ref} not found")
            out.append(self._parsed_doc(project, row))
        (sa, ia), (sb, ib) = out
        inv_diff = compare_inventories(ia, ib)
        return dict(a=a, b=b, summary=summarize(inv_diff), inventory=inv_diff, sections=compare_sections(sa, sb))

    def export(self, project, kind, fmt):
        data = self.inventory(project, kind) if kind != "findings" else self.findings(project)
        flat = []
        for d in data:
            row = {k: (", ".join(v) if isinstance(v, list) else v) for k, v in d.items() if k not in ("prov", "evidence")}
            p = d.get("prov") or (d.get("evidence") or [{}])[0]
            row["source"] = f"{p.get('doc_id','')} v{p.get('version','')} section {p.get('sec_id','')} page {p.get('page','')}".strip()
            flat.append(row)
        if fmt == "json":
            return json.dumps(flat, indent=2), "application/json"
        buf = io.StringIO()
        if flat:
            w = csv.DictWriter(buf, fieldnames=list(flat[0].keys()))
            w.writeheader()
            w.writerows(flat)
        return buf.getvalue(), "text/csv"

    def stats(self, project):
        st = self._project_state(project)
        docs = self.documents(project)
        f = self.findings(project)
        openf = [x for x in f if x["status"] == "open"]
        return dict(
            documents=len(docs), active_documents=len([d for d in docs if d["status"] == "approved"]),
            chunks=len(st["index"].chunks), components=len({c["id"] for c in st["inv"]["components"]}),
            interfaces=len({c["id"] for c in st["inv"]["interfaces"]}), signals=len({c["id"] for c in st["inv"]["signals"]}),
            ports=len(st["inv"]["ports"]), open_findings=len(openf), high=len([x for x in openf if x["severity"] == "high"]),
            questions=self.db.one("SELECT COUNT(*) AS n FROM answers WHERE project=?", (project,))["n"])

    def system_info(self) -> dict:
        st = None
        notes = []
        try:
            st = self._project_state("PRJ-BODY")
            notes = st["index"].notes
            emb, store = st["index"].embedder.name, st["index"].store.backend
        except Exception:
            emb, store = self.cfg["embedding"]["provider"], self.cfg["vector_store"]["backend"]
        return dict(embedding=emb, vector_store=store, llm=self.cfg["llm"]["provider"] +
                    (":" + self.cfg["llm"]["ollama_model"] if self.cfg["llm"]["provider"] == "ollama" else ""),
                    external_calls="none (fully local)" if self.cfg["llm"]["provider"] == "extractive" else "local Ollama server only", notes=notes)
