"""Migration Planner & Reconciliation Workbench: stdlib-only backend (HTTP + SQLite + optional Claude agent)."""
import datetime, hashlib, json, mimetypes, os, re, sqlite3, sys, time, urllib.request
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_SAMPLE = 50
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
def DB(): return os.environ.get("DB_PATH", "migration.db")
def MODEL(): return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

def jlog(event, **kw):  # structured application log (JSON lines)
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **kw}), file=sys.stdout, flush=True)

SRC = [("cust_id","string"),("full_name","string"),("email","string"),("phone","string"),("dob","string DD/MM/YYYY"),("country","string name"),("status","A|I|S"),("balance",'string "1,234.50"'),("created","string YYYY-MM-DD HH:MM"),("notes","free text")]
TGT = [("id","int",1),("first_name","text",1),("last_name","text",0),("email","email",1),("phone_e164","phone",0),("birth_date","date",0),("country_code","iso2",1),("is_active","bool",1),("balance_cents","int",1),("created_at","timestamp",1),("segment","text",0)]
SRC_NAMES = [s[0] for s in SRC]; TGT_NAMES = [t[0] for t in TGT]

def build_sample():
    N = "Asha Rao,Ben Carter,Chen Wei,Dina Haddad,Ravi Singh,Elena Petrova,Farid Khan,Grace Lee,Hiro Tanaka,Ines Alves,Jon Smith,Kavya Nair,Liam OBrien,Maya Gupta,Noor Ali,Omar Sheikh,Priya Iyer,Quinn Davis,Rita Costa,Sam Patel,Tara Bose,Asha Rao".split(",")
    C = "India,United States,India,Germany,India,Brazil,India,United Kingdom,Japan,Brazil,Canada,India,Ireland,India,France,India,India,Canada,Brazil,India,India,India".split(",")
    rows = [dict(cust_id=str(1001 + i if i < 21 else 1001), full_name=n, email=re.sub("[^a-z]+", ".", n.lower()) + "@example.com",
                 phone="98" + str(10000000 + i * 7919)[:8], dob=f"{1+i%28:02d}/{1+i%12:02d}/{1970+i}", country=C[i],
                 status="I" if i % 5 == 0 else "A", balance=f"{i*1234.5+10:,.2f}", created=f"2024-03-{1+i%28:02d} 10:{i*2%60:02d}",
                 notes="" if i % 4 else "VIP") for i, n in enumerate(N)]
    rows[3]["status"] = "S"; rows[6]["email"] = "farid.khan(at)example.com"; rows[9]["dob"] = "31/02/1980"
    rows[12]["balance"] = "12O.50"; rows[15]["phone"] = "12ab"; rows[17]["full_name"] = ""; rows[19]["country"] = "Atlantis"
    assert len(rows) <= MAX_SAMPLE
    return rows
SAMPLE = build_sample()

class RuleError(Exception): pass
def E(m): raise RuleError(m)
def _dmy(v):
    m = re.fullmatch(r"(\d\d)/(\d\d)/(\d{4})", str(v)) or E("bad date format")
    try: return datetime.date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    except ValueError: E("impossible calendar date")
def _money(v):
    s = str(v).strip()
    if not re.fullmatch(r"\d{1,3}(,\d{3})*(\.\d{1,2})?|\d+(\.\d{1,2})?", s): E("malformed amount")
    return int((Decimal(s.replace(",", "")) * 100).to_integral_value())
def _phone(v):
    s = re.sub(r"[\s-]", "", str(v)); return "+91" + s if re.fullmatch(r"\d{10}", s) else E("not a 10-digit number")
ISO = {"india":"IN","united states":"US","germany":"DE","brazil":"BR","united kingdom":"GB","japan":"JP","canada":"CA","ireland":"IE","france":"FR"}
RULES = {  # closed set: no arbitrary code
 "copy": ("Copy value unchanged", lambda v: v), "trim": ("Trim whitespace", lambda v: str(v).strip()),
 "lower": ("Trim + lowercase", lambda v: str(v).strip().lower()),
 "to_int": ("Parse integer", lambda v: int(v) if re.fullmatch(r"\d+", str(v).strip()) else E("not an integer")),
 "first_name": ("First token of full name", lambda v: (str(v).split() or E("empty name"))[0]),
 "last_name": ("Remaining tokens of full name", lambda v: " ".join(str(v).split()[1:]) or None),
 "dmy_iso": ("DD/MM/YYYY to YYYY-MM-DD (calendar-checked)", _dmy),
 "country_iso2": ("Country name to ISO-2 via lookup", lambda v: ISO.get(str(v).strip().lower()) or E("unknown country")),
 "status_bool": ("A true, I false, else error", lambda v: True if v == "A" else False if v == "I" else E(f'unmapped status code "{v}"')),
 "status_bool_lenient": ("A true; I and S false", lambda v: True if v == "A" else False if v in ("I", "S") else E("unmapped status code")),
 "money_cents": ('"1,234.50" to 123450 cents', _money), "phone_e164": ("10 digits to +91XXXXXXXXXX", _phone),
 "ts_iso": ("YYYY-MM-DD HH:MM to ISO-8601 UTC", lambda v: str(v).replace(" ", "T") + ":00Z" if re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", str(v)) else E("bad timestamp")),
}
CHECK = {"int": lambda v: isinstance(v, int) and not isinstance(v, bool), "text": lambda v: isinstance(v, str) and v != "",
 "email": lambda v: bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", v)), "phone": lambda v: bool(re.fullmatch(r"\+\d{8,15}", v)),
 "date": lambda v: bool(re.fullmatch(r"\d{4}-\d\d-\d\d", v)), "iso2": lambda v: bool(re.fullmatch(r"[A-Z]{2}", v)),
 "bool": lambda v: isinstance(v, bool), "timestamp": lambda v: v.endswith("Z")}

def map_hash(m): return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()[:8]

def transform(mp, row):
    out, errs = {}, []
    for t, ty, rq in TGT:
        m = mp.get(t) or {}; val = None
        if m.get("src") and m.get("rule"):
            try: val = RULES[m["rule"]][1](row[m["src"]])
            except RuleError as e:
                errs.append(dict(field=t, source_field=m["src"], source_value=row[m["src"]], rule=m["rule"], message=str(e))); continue
        if val is None or val == "":
            if rq: errs.append(dict(field=t, source_field=m.get("src"), source_value=row.get(m.get("src")) if m.get("src") else None, rule=m.get("rule"), message="required target field is null"))
            out[t] = None; continue
        if not CHECK[ty](val): errs.append(dict(field=t, source_field=m["src"], source_value=row[m["src"]], rule=m["rule"], message=f"fails target type {ty}"))
        out[t] = val
    return out, errs

def dry_run(mp):
    seen, acc, rej = set(), [], []
    for i, r in enumerate(SAMPLE):
        out, errs = transform(mp, r)
        if not errs:
            if out["id"] in seen: errs.append(dict(field="id", source_field="cust_id", source_value=r["cust_id"], rule="unique", message="duplicate primary key within batch"))
            else: seen.add(out["id"])
        (rej.append(dict(row=i+1, src=r, errs=errs)) if errs else acc.append(dict(row=i+1, rec=out)))
    res = dict(src=len(SAMPLE), tr=len(SAMPLE), acc=acc, rej=rej, map_hash=map_hash(mp))
    res["hash"] = hashlib.sha256(json.dumps([acc, rej], sort_keys=True).encode()).hexdigest()[:8]
    return res

# ---------- agent tools (read-only inspection/validation) ----------
def run_tool(name, a):
    if name == "inspect_schema": return SRC_NAMES if a.get("which") == "source" else TGT_NAMES
    if name == "sample_values":
        if a.get("field") not in SRC_NAMES: return {"error": "unknown source field"}
        return [r[a["field"]] for r in SAMPLE[:3]]
    if name == "validate_rule":
        f, rl = a.get("field"), a.get("rule")
        if f not in SRC_NAMES or rl not in RULES: return {"error": "unknown field or unsupported rule"}
        ok, bad = 0, []
        for r in SAMPLE:
            try: RULES[rl][1](r[f]); ok += 1
            except RuleError as e: bad.append(f"{r['cust_id']}: {e}")
        return {"ok": ok, "total": len(SAMPLE), "failures": bad[:8]}
    return {"error": f"tool {name} is not available"}
TOOLS = [
 {"name":"inspect_schema","description":"List field names of the source or target schema.","input_schema":{"type":"object","properties":{"which":{"enum":["source","target"]}},"required":["which"]}},
 {"name":"sample_values","description":"First 3 sample values of a source field.","input_schema":{"type":"object","properties":{"field":{"type":"string"}},"required":["field"]}},
 {"name":"validate_rule","description":"Apply a supported rule to all sample values of a source field; returns pass count and failure messages.","input_schema":{"type":"object","properties":{"field":{"type":"string"},"rule":{"type":"string"}},"required":["field","rule"]}}]
SYSTEM = ("You plan a migration from legacy_customers to customers. Use ONLY the provided tools. Supported rules: "
 + "; ".join(f"{k} ({v[0]})" for k, v in RULES.items()) + ". Target fields: " + json.dumps([(t[0], t[1], "required" if t[2] else "optional") for t in TGT]) +
 ' Inspect schemas, sample values, and validate every rule you propose. Reply with ONLY JSON: {"mappings":{"<target>":{"src":"<source field or null>","rule":"<rule or null>"}},"risks":[["high|medium|low","text"]],"questions":["text"]}. You cannot approve or execute anything; a human reviews the plan.')

def llm_post(body, key):
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", json.dumps(body).encode(), {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)

def validate_plan(o):
    mp = {}
    for t in TGT_NAMES:
        m = (o.get("mappings") or {}).get(t) or {}
        s, r = m.get("src"), m.get("rule")
        if s and (s not in SRC_NAMES or r not in RULES): raise ValueError(f"invalid mapping for {t}: {s}/{r}")
        mp[t] = {"src": s or None, "rule": r if s else None}
    risks = [[str(x[0]), str(x[1])] for x in o.get("risks", [])]; qs = [str(q) for q in o.get("questions", [])]
    return mp, risks, qs

def llm_agent():
    key = os.environ["ANTHROPIC_API_KEY"]; calls = []
    msgs = [{"role": "user", "content": "Propose the field mapping and risks."}]
    for _ in range(12):
        r = llm_post({"model": MODEL(), "max_tokens": 3000, "system": SYSTEM, "tools": TOOLS, "messages": msgs}, key)
        msgs.append({"role": "assistant", "content": r["content"]})
        tu = [b for b in r["content"] if b["type"] == "tool_use"]
        if not tu:
            txt = "".join(b.get("text", "") for b in r["content"]); o = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
            return validate_plan(o), calls
        res = []
        for b in tu:
            out = run_tool(b["name"], b["input"]); s = json.dumps(out)[:120]
            calls.append([f'{b["name"]}({json.dumps(b["input"])})', s]); jlog("agent.tool_call", tool=b["name"], input=b["input"], error="error" in out if isinstance(out, dict) else False)
            res.append({"type": "tool_result", "tool_use_id": b["id"], "content": json.dumps(out)})
        msgs.append({"role": "user", "content": res})
    raise RuntimeError("tool loop limit reached")

ALIAS = {"id":("cust_id","to_int"),"first_name":("full_name","first_name"),"last_name":("full_name","last_name"),"email":("email","lower"),"phone_e164":("phone","phone_e164"),"birth_date":("dob","dmy_iso"),"country_code":("country","country_iso2"),"is_active":("status","status_bool"),"balance_cents":("balance","money_cents"),"created_at":("created","ts_iso")}
def heuristic_agent():
    mp, risks, qs, calls = {}, [], [], []
    for t, ty, rq in TGT:
        s, r = ALIAS.get(t, (None, None)); mp[t] = {"src": s, "rule": r}
        if not s: risks.append(["high" if rq else "low", f'Target "{t}" has no source field.']); qs.append(f'What should populate "{t}"?'); continue
        v = run_tool("validate_rule", {"field": s, "rule": r}); calls.append([f'validate_rule("{s}","{r}")', f'{v["ok"]}/{v["total"]} pass'])
        if v["failures"]: risks.append(["medium", f'{t}: rule {r} rejects {v["total"]-v["ok"]} sample rows.'])
    risks += [["medium", 'Source "notes" has no target and is dropped.'], ["medium", "phone_e164 assumes +91."]]
    qs.append('Status "S" has no target equivalent: treat as inactive (status_bool_lenient) or quarantine?')
    return (mp, risks, qs), calls

# ---------- persistence ----------
SCHEMA = """CREATE TABLE IF NOT EXISTS versions(v INTEGER PRIMARY KEY, map TEXT, note TEXT, status TEXT, hash TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS dry(v INTEGER PRIMARY KEY, result TEXT);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, v INTEGER, attempt INTEGER, ins INTEGER, skip INTEGER, status TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS target(id INTEGER PRIMARY KEY, v INTEGER, run INTEGER, rec TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, t TEXT, type TEXT, d TEXT);
CREATE TABLE IF NOT EXISTS agent(id INTEGER PRIMARY KEY, data TEXT);"""
def conn():
    c = sqlite3.connect(DB()); c.row_factory = sqlite3.Row; c.executescript(SCHEMA); return c
def now(): return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
def audit(c, typ, d): c.execute("INSERT INTO events(t,type,d) VALUES(?,?,?)", (now()[11:], typ, d)); jlog("audit." + typ, detail=d)

class ApiErr(Exception):
    def __init__(s, code, msg): super().__init__(msg); s.code = code

def get_v(c, v):
    r = c.execute("SELECT * FROM versions WHERE v=?", (v,)).fetchone()
    if not r: raise ApiErr(404, f"version {v} not found")
    return r
def new_version(c, mp, note):
    n = (c.execute("SELECT MAX(v) FROM versions").fetchone()[0] or 0) + 1
    c.execute("INSERT INTO versions VALUES(?,?,?,?,?,?)", (n, json.dumps(mp), note, "draft", map_hash(mp), now()[:16])); return n

def preview(mp):
    out = {}
    for t, m in mp.items():
        if m.get("src") and m.get("rule"):
            r = run_tool("validate_rule", {"field": m["src"], "rule": m["rule"]}); ex = None
            try: ex = json.dumps(RULES[m["rule"]][1](SAMPLE[0][m["src"]]))
            except RuleError as e: ex = "error: " + str(e)
            out[t] = {"ok": r["ok"], "total": r["total"], "example": f'{SAMPLE[0][m["src"]]} -> {ex}'}
    return out

def recon(c, v, d):
    rows = [json.loads(r["rec"]) for r in c.execute("SELECT rec FROM target WHERE v=?", (v,))]
    return {"in_target": len(rows), "sum_target": sum(r["balance_cents"] for r in rows), "sum_expected": sum(a["rec"]["balance_cents"] for a in d["acc"]) if d else 0,
            "match": bool(d) and len(rows) == len(d["acc"]) and sum(r["balance_cents"] for r in rows) == sum(a["rec"]["balance_cents"] for a in d["acc"])}

def state(c):
    vs = [dict(r) for r in c.execute("SELECT * FROM versions ORDER BY v")]
    for x in vs: x["map"] = json.loads(x["map"])
    dry = {r["v"]: json.loads(r["result"]) for r in c.execute("SELECT * FROM dry")}
    ag = c.execute("SELECT data FROM agent WHERE id=1").fetchone()
    return {"meta": {"src": SRC, "tgt": TGT, "sample": SAMPLE, "max": MAX_SAMPLE, "rules": {k: v[0] for k, v in RULES.items()}, "llm": bool(os.environ.get("ANTHROPIC_API_KEY"))},
        "versions": vs, "preview": {x["v"]: preview(x["map"]) for x in vs}, "dry": dry,
        "recon": {x["v"]: recon(c, x["v"], dry.get(x["v"])) for x in vs},
        "runs": [{**dict(r), "id": f"run{r['id']}"} for r in c.execute("SELECT * FROM runs ORDER BY id DESC")],
        "hist": [{"t": r["t"], "type": r["type"], "d": r["d"]} for r in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 200")], "agent": json.loads(ag["data"]) if ag else None}

# ---------- actions ----------
def act_agent(c):
    mode, err = "llm", None
    try:
        if not os.environ.get("ANTHROPIC_API_KEY"): raise RuntimeError("ANTHROPIC_API_KEY not set")
        (mp, risks, qs), calls = llm_agent()
    except Exception as e:
        mode, err = "heuristic-fallback", f"{type(e).__name__}: {e}"; jlog("agent.fallback", error=err)
        (mp, risks, qs), calls = heuristic_agent()
    n = new_version(c, mp, f"Agent proposal ({mode})")
    c.execute("INSERT OR REPLACE INTO agent VALUES(1,?)", (json.dumps({"calls": calls, "risks": risks, "qs": qs, "mode": mode, "error": err}),))
    audit(c, "plan", f"v{n} drafted by agent ({mode}, {len(calls)} tool calls)"); return {"v": n, "mode": mode}

def act_edit(c, v, body):
    r = get_v(c, v)
    if r["status"] != "draft": raise ApiErr(409, "approved versions are locked; clone to a new version")
    try: mp, _, _ = validate_plan({"mappings": body.get("map") or {}})
    except ValueError as e: raise ApiErr(400, str(e))
    c.execute("UPDATE versions SET map=?, hash=? WHERE v=?", (json.dumps(mp), map_hash(mp), v)); c.execute("DELETE FROM dry WHERE v=?", (v,))
    audit(c, "plan", f"v{v} edited"); return {}

def act_dry(c, v):
    r = get_v(c, v); d = dry_run(json.loads(r["map"])); c.execute("INSERT OR REPLACE INTO dry VALUES(?,?)", (v, json.dumps(d)))
    audit(c, "dry-run", f"v{v}: {len(d['acc'])} accepted, {len(d['rej'])} quarantined (result {d['hash']})"); return {"hash": d["hash"]}

def act_approve(c, v):
    r = get_v(c, v); mp = json.loads(r["map"])
    if r["status"] == "approved": raise ApiErr(409, "already approved")
    bad = [t for t, _, rq in TGT if rq and not (mp.get(t, {}).get("src") and mp[t].get("rule"))]
    if bad: raise ApiErr(400, "required fields unmapped: " + ", ".join(bad))
    d = c.execute("SELECT result FROM dry WHERE v=?", (v,)).fetchone()
    if not d or json.loads(d["result"])["map_hash"] != r["hash"]: raise ApiErr(400, "run a current dry run before approving")
    c.execute("UPDATE versions SET status='approved' WHERE v=?", (v,)); audit(c, "approval", f"v{v} approved (hash {r['hash']})"); return {}

def act_clone(c, v):
    r = get_v(c, v); n = new_version(c, json.loads(r["map"]), f"Edited from v{v}"); audit(c, "plan", f"v{n} saved from v{v}"); return {"v": n}

def act_execute(c, v, body):
    r = get_v(c, v)
    if r["status"] != "approved": raise ApiErr(403, "execution requires an approved plan")
    d = c.execute("SELECT result FROM dry WHERE v=?", (v,)).fetchone(); d = json.loads(d["result"]) if d else None
    if not d or d["map_hash"] != r["hash"]: raise ApiErr(409, "dry run missing or stale")
    other = c.execute("SELECT DISTINCT v FROM target WHERE v!=?", (v,)).fetchone()
    if other: raise ApiErr(409, f"roll back v{other[0]} first (one live version at a time)")
    last = c.execute("SELECT status FROM runs WHERE v=? ORDER BY id DESC LIMIT 1", (v,)).fetchone()
    if last and last["status"] == "complete": raise ApiErr(409, "migration already complete; nothing to retry")
    attempt = c.execute("SELECT COUNT(*) FROM runs WHERE v=? AND status!='rolled_back'", (v,)).fetchone()[0] + 1
    run_id = (c.execute("SELECT MAX(id) FROM runs").fetchone()[0] or 0) + 1; ins = skip = 0; status = "complete"
    for a in d["acc"]:
        if body.get("simulate_failure") and ins >= 10: status = "partial"; break
        cur = c.execute("INSERT OR IGNORE INTO target VALUES(?,?,?,?)", (a["rec"]["id"], v, run_id, json.dumps(a["rec"])))
        ins += cur.rowcount; skip += 1 - cur.rowcount
    c.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", (run_id, v, attempt, ins, skip, status, now()[11:]))
    audit(c, "retry" if attempt > 1 else "execute", f"run{run_id} v{v} attempt {attempt}: inserted {ins}, skipped {skip} existing, {status}"); return {"status": status}

def act_rollback(c, v):
    get_v(c, v); n = c.execute("DELETE FROM target WHERE v=?", (v,)).rowcount
    if not n: raise ApiErr(409, "nothing to roll back")
    c.execute("INSERT INTO runs(v,attempt,ins,skip,status,at) VALUES(?,?,?,?,?,?)", (v, 0, -n, 0, "rolled_back", now()[11:]))
    audit(c, "rollback", f"v{v}: removed {n} records"); return {"removed": n}

def handle(method, path, body=None):
    body = body or {}
    try:
        with conn() as c:
            if path == "/healthz": return 200, {"ok": True}
            if path == "/api/state" and method == "GET": return 200, state(c)
            if path == "/api/agent" and method == "POST": return 200, act_agent(c)
            if path == "/api/reset" and method == "POST":
                for t in ("versions", "dry", "runs", "target", "events", "agent"): c.execute(f"DELETE FROM {t}")
                jlog("reset"); return 200, {}
            m = re.fullmatch(r"/api/versions/(\d+)(?:/(\w+))?", path)
            if m:
                v, a = int(m[1]), m[2]
                if method == "PUT" and not a: return 200, act_edit(c, v, body)
                if method == "POST" and a in ("dry", "approve", "clone", "rollback"): return 200, {"dry": act_dry, "approve": act_approve, "clone": act_clone, "rollback": act_rollback}[a](c, v)
                if method == "POST" and a == "execute": return 200, act_execute(c, v, body)
            return 404, {"error": "not found"}
    except ApiErr as e: jlog("api.error", path=path, code=e.code, error=str(e)); return e.code, {"error": str(e)}
    except Exception as e: jlog("api.exception", path=path, error=repr(e)); return 500, {"error": "internal error"}

class H(BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json"):
        b = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _api(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        try: body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError: return self._send(400, {"error": "invalid JSON"})
        t0 = time.time(); code, obj = handle(method, self.path.split("?")[0], body)
        jlog("http", method=method, path=self.path, status=code, ms=round((time.time() - t0) * 1000)); self._send(code, obj)
    def do_POST(self): self._api("POST")
    def do_PUT(self): self._api("PUT")
    def do_GET(self):
        p = self.path.split("?")[0]
        if p.startswith("/api/") or p == "/healthz": return self._api("GET")
        f = os.path.realpath(os.path.join(STATIC, "index.html" if p == "/" else p.lstrip("/")))
        if not f.startswith(STATIC) or not os.path.isfile(f): return self._send(404, {"error": "not found"})
        self._send(200, open(f, "rb").read(), mimetypes.guess_type(f)[0] or "application/octet-stream")
    def log_message(self, *a): pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000")); jlog("startup", port=port, llm=bool(os.environ.get("ANTHROPIC_API_KEY")))
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
