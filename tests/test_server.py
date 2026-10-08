import os, sys, tempfile, unittest, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S

def call(m, p, b=None): return S.handle(m, p, b)

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = os.path.join(self.tmp, "t.db"); os.environ.pop("ANTHROPIC_API_KEY", None)
        S.jlog = lambda *a, **k: None
        self.v = call("POST", "/api/agent")[1]["v"]
    def full(self, v=None):
        v = v or self.v; call("POST", f"/api/versions/{v}/dry"); return call("POST", f"/api/versions/{v}/approve")
    def st(self): return call("GET", "/api/state")[1]

class Rules(Base):
    def test_date_rejects_impossible(self):
        with self.assertRaises(S.RuleError): S.RULES["dmy_iso"][1]("31/02/1980")
    def test_money_exact(self): self.assertEqual(S.RULES["money_cents"][1]("1,234.50"), 123450)
    def test_status_strict_vs_lenient(self):
        with self.assertRaises(S.RuleError): S.RULES["status_bool"][1]("S")
        self.assertFalse(S.RULES["status_bool_lenient"][1]("S"))

class DryRun(Base):
    def test_counts_and_evidence(self):
        call("POST", f"/api/versions/{self.v}/dry"); d = self.st()["dry"][self.v]
        self.assertEqual(d["src"], len(S.SAMPLE)); self.assertEqual(len(d["acc"]) + len(d["rej"]), d["src"])
        self.assertTrue(all(e["field"] and "message" in e and "source_value" in e for r in d["rej"] for e in r["errs"]))
        self.assertTrue(any(e["message"].startswith("duplicate") for r in d["rej"] for e in r["errs"]))
    def test_deterministic(self):
        a = call("POST", f"/api/versions/{self.v}/dry")[1]["hash"]; b = call("POST", f"/api/versions/{self.v}/dry")[1]["hash"]; self.assertEqual(a, b)

class Gates(Base):
    def test_cannot_approve_without_dry_run(self): self.assertEqual(call("POST", f"/api/versions/{self.v}/approve")[0], 400)
    def test_cannot_execute_unapproved(self):
        call("POST", f"/api/versions/{self.v}/dry"); self.assertEqual(call("POST", f"/api/versions/{self.v}/execute", {})[0], 403)
    def test_edit_invalidates_dry_run_and_locks_after_approval(self):
        self.full(); self.assertEqual(call("PUT", f"/api/versions/{self.v}", {"map": {}})[0], 409)
        n = call("POST", f"/api/versions/{self.v}/clone")[1]["v"]; call("PUT", f"/api/versions/{n}", {"map": self.st()["versions"][0]["map"]})
        self.assertNotIn(n, self.st()["dry"]); self.assertEqual(call("POST", f"/api/versions/{n}/execute", {})[0], 403)
    def test_rejects_unsupported_rule(self):
        n = call("POST", f"/api/versions/{self.v}/clone")[1]["v"]
        self.assertEqual(call("PUT", f"/api/versions/{n}", {"map": {"email": {"src": "email", "rule": "eval"}}})[0], 400)

class Execution(Base):
    def test_retry_is_idempotent_and_reconciles(self):
        self.full(); call("POST", f"/api/versions/{self.v}/execute", {"simulate_failure": True})
        s = self.st(); self.assertEqual(s["runs"][0]["status"], "partial"); self.assertEqual(s["recon"][self.v]["in_target"], 10)
        code, r = call("POST", f"/api/versions/{self.v}/execute", {}); self.assertEqual(r["status"], "complete")
        s = self.st(); acc = len(s["dry"][self.v]["acc"]); self.assertEqual(s["recon"][self.v]["in_target"], acc); self.assertTrue(s["recon"][self.v]["match"])
        self.assertEqual(s["runs"][0]["skip"], 10); self.assertEqual(call("POST", f"/api/versions/{self.v}/execute", {})[0], 409)
    def test_rollback_and_history(self):
        self.full(); call("POST", f"/api/versions/{self.v}/execute", {}); self.assertEqual(call("POST", f"/api/versions/{self.v}/rollback")[0], 200)
        s = self.st(); self.assertEqual(s["recon"][self.v]["in_target"], 0)
        types = [h["type"] for h in s["hist"]]; self.assertTrue({"approval", "execute", "rollback", "dry-run"} <= set(types))

class Agent(Base):
    def test_fallback_without_key(self): self.assertEqual(self.st()["agent"]["mode"], "heuristic-fallback")
    def _llm(self, final):
        os.environ["ANTHROPIC_API_KEY"] = "test"; seq = [
            {"content": [{"type": "tool_use", "id": "1", "name": "validate_rule", "input": {"field": "email", "rule": "lower"}}]},
            {"content": [{"type": "text", "text": final}]}]
        S.llm_post = lambda body, key: seq.pop(0); return call("POST", "/api/agent")[1]
    def test_llm_tool_loop_and_validated_output(self):
        r = self._llm(json.dumps({"mappings": {"id": {"src": "cust_id", "rule": "to_int"}}, "risks": [["low", "x"]], "questions": ["q"]}))
        self.assertEqual(r["mode"], "llm"); a = self.st()["agent"]; self.assertEqual(a["calls"][0][0].split("(")[0], "validate_rule")
    def test_invalid_llm_output_falls_back(self):
        r = self._llm(json.dumps({"mappings": {"id": {"src": "cust_id", "rule": "os.system"}}})); self.assertEqual(r["mode"], "heuristic-fallback")
    def test_agent_cannot_use_unlisted_tool(self): self.assertIn("error", S.run_tool("approve_plan", {}))

if __name__ == "__main__": unittest.main()
