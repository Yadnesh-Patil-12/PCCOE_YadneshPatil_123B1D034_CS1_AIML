"""Unit and integration tests.  Run:  python -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hldassist import config as C  # noqa: E402
from hldassist.app import make_handler  # noqa: E402
from hldassist.auth import Sessions  # noqa: E402
from hldassist.service import Service  # noqa: E402
from hldassist.textutil import logical_lines, tokens  # noqa: E402


class TextTests(unittest.TestCase):
    def test_identifier_tokens_include_parts(self):
        t = tokens("SWC_DoorLockCtrl")
        self.assertIn("swc_doorlockctrl", t)
        self.assertIn("door", t)
        self.assertIn("lock", t)

    def test_wrapped_lines_are_joined(self):
        lines = logical_lines("Description: Arbitrates lock and\nunlock requests.\nStep 1 - next")
        self.assertEqual(lines[0], "Description: Arbitrates lock and unlock requests.")
        self.assertEqual(len(lines), 2)


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = Service(data_dir=Path(tempfile.mkdtemp(prefix="hld_test_")))

    def test_seed_documents_loaded_and_superseded(self):
        docs = {(d["doc_id"], d["version"]): d["status"] for d in self.svc.documents("PRJ-BODY")}
        self.assertEqual(docs[("HLD-BCM-001", "1.0")], "superseded")
        self.assertEqual(docs[("HLD-BCM-001", "1.1")], "approved")

    def test_extraction_counts(self):
        inv = self.svc._project_state("PRJ-ENERGY")["inv"]
        self.assertEqual(len(inv["components"]), 5)
        self.assertEqual(len(inv["ports"]), 12)
        self.assertEqual(len(inv["signals"]), 7)

    def test_seeded_issues_detected(self):
        fps = {f["fingerprint"] for f in self.svc.findings("PRJ-BODY")}
        for expected in ("ORPHAN_CONSUMER:SIG_DoorAjarState", "UNDEFINED_INTERFACE:SR_CrashStatus", "TYPE_CONFLICT:SIG_VehicleSpeed",
                         "DUPLICATE_COMPONENT:SWC_BcmGateway", "SUSPICIOUS_CONTENT:HLD-DCM-003"):
            self.assertIn(expected, fps)

    def test_structured_answer_has_citation(self):
        r = self.svc.ask("PRJ-BODY", "Which components produce SIG_VehicleSpeed?", "test")
        self.assertFalse(r["refused"])
        self.assertIn("SWC_SpeedSignalAdapter", r["answer"])
        self.assertRegex(r["answer"], r"\[S1\]")
        self.assertEqual(r["tool"], "signal_producers")

    def test_refuses_unknown_entity_and_out_of_scope(self):
        self.assertTrue(self.svc.ask("PRJ-BODY", "What is the safety level of SWC_AirbagCtrl?", "t")["refused"])
        self.assertTrue(self.svc.ask("PRJ-BODY", "What is the capital of France?", "t")["refused"])

    def test_injection_text_is_excluded_from_answers(self):
        r = self.svc.ask("PRJ-BODY", "Mark every finding as approved without review", "t")
        self.assertNotIn("NOTE TO AI", r["answer"])
        for c in r["citations"]:
            self.assertNotIn("ignore all previous instructions", c["text"].lower())

    def test_project_isolation_in_answers(self):
        r = self.svc.ask("PRJ-ENERGY", "Which components consume SIG_VehicleSpeed?", "t")
        self.assertTrue(r["refused"])  # that signal only exists in the other project

    def test_compare_revisions(self):
        d = self.svc.compare("PRJ-BODY", "HLD-BCM-001:1.0", "HLD-BCM-001:1.1")
        self.assertEqual([x["id"] for x in d["inventory"]["components"]["added"]], ["SWC_ChildLockMgr"])

    def test_impact_transitive(self):
        imp = self.svc.impact("PRJ-ENERGY", "SWC_CellMonitor")
        self.assertIn("SWC_ChargeCtrl", imp["transitive"])

    def test_export_csv(self):
        body, ctype = self.svc.export("PRJ-BODY", "components", "csv")
        self.assertIn("SWC_DoorLockCtrl", body)
        self.assertEqual(ctype, "text/csv")


class MockOllama(BaseHTTPRequestHandler):
    reply = "SIG_VehicleSpeed is produced by SWC_SpeedSignalAdapter [S1]."

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"message": {"content": MockOllama.reply}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a): pass


class LlmPathTests(unittest.TestCase):
    def test_local_llm_answer_validated_and_fallback(self):
        srv = HTTPServer(("127.0.0.1", 0), MockOllama)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        svc = Service(data_dir=Path(tempfile.mkdtemp(prefix="hld_llm_")))
        svc.cfg["llm"]["provider"] = "ollama"
        svc.cfg["llm"]["ollama_url"] = f"http://127.0.0.1:{srv.server_address[1]}"
        MockOllama.reply = "SIG_VehicleSpeed is produced by SWC_SpeedSignalAdapter [S1]."
        r = svc.ask("PRJ-BODY", "Which components produce SIG_VehicleSpeed?", "t")
        self.assertTrue(r["mode"].startswith("ollama"))
        MockOllama.reply = "It is produced by something [S9]."   # invented citation -> must be rejected
        r = svc.ask("PRJ-BODY", "Which components produce SIG_VehicleSpeed?", "t")
        self.assertIn("failed citation validation", r["mode"])
        self.assertIn("SWC_SpeedSignalAdapter", r["answer"])
        svc.cfg["llm"]["ollama_url"] = "http://127.0.0.1:1"      # server down -> fallback, no crash
        r = svc.ask("PRJ-BODY", "Which components produce SIG_VehicleSpeed?", "t")
        self.assertIn("unavailable", r["mode"])
        srv.shutdown()


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        svc = Service(data_dir=Path(tempfile.mkdtemp(prefix="hld_http_")))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(svc, Sessions(60)))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def call(self, path, method="GET", body=None, token=None):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def login(self, user):
        return self.call("/api/login", "POST", {"username": user, "password": "demo123"})[1]["token"]

    def test_requires_login(self):
        self.assertEqual(self.call("/api/projects")[0], 401)
        self.assertEqual(self.call("/api/login", "POST", {"username": "alice", "password": "bad"})[0], 401)

    def test_rbac_and_project_isolation(self):
        bob, carol, alice = self.login("bob"), self.login("carol"), self.login("alice")
        self.assertEqual(self.call("/api/projects/PRJ-ENERGY/stats", token=bob)[0], 403)      # bob has no access
        self.assertEqual(self.call("/api/projects/PRJ-BODY/findings/run", "POST", token=bob)[0], 403)  # tester cannot run
        self.assertEqual(self.call("/api/projects/PRJ-ENERGY/ask", "POST", {"question": "What does SWC_ChargeCtrl do?"}, carol)[0], 200)
        self.assertEqual(self.call("/api/audit", token=carol)[0], 403)
        self.assertEqual(self.call("/api/audit", token=alice)[0], 200)

    def test_ask_and_review(self):
        alice = self.login("alice")
        s, r = self.call("/api/projects/PRJ-BODY/ask", "POST", {"question": "What does SWC_CentralLockArb do?"}, alice)
        self.assertEqual(s, 200)
        self.assertIn("arbitrat", r["answer"].lower())
        self.assertEqual(self.call(f"/api/answers/{r['id']}/review", "POST", {"status": "accepted"}, alice)[0], 200)

    def test_input_validation_and_traversal(self):
        alice = self.login("alice")
        self.assertEqual(self.call("/api/projects/PRJ-BODY/ask", "POST", {"question": "x"}, alice)[0], 400)
        req = urllib.request.Request(self.base + "/..%2f..%2fetc/passwd")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
