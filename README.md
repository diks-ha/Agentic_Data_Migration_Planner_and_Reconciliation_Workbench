# Agentic Data Migration Planner & Reconciliation Workbench

Plans, validates and executes the migration of one bounded dataset (`legacy_customers` → `customers`, sample ≤ **50** records, 22 shipped) into a **mock** SQLite target. A Claude agent proposes the mapping; a human must approve it before anything runs.

## Run locally
```
python3 server.py            # http://localhost:8000  (Python 3.10+, no dependencies)
python3 -m unittest discover -s tests
cp .env.example .env         # optional: export ANTHROPIC_API_KEY to enable the live agent
```
Without `ANTHROPIC_API_KEY` the "Run agent" button uses a deterministic heuristic planner (clearly labelled `heuristic-fallback` in the UI and audit log).

## Architecture
- `server.py`: stdlib HTTP server + SQLite. Owns all business logic: closed rule set, deterministic dry run, quarantine, approval gate, idempotent execution, reconciliation, rollback, audit events, agent tool loop. JSON-line logs on stdout (`http`, `audit.*`, `agent.tool_call`, `agent.fallback`, `api.error`).
- `static/`: multipage frontend (six HTML pages sharing `app.js`/`style.css`) that renders server state; no logic duplicated client-side.
- Tables: `versions`, `dry`, `runs`, `target`, `events`, `agent`.
- **Agent boundary:** the LLM can call only `inspect_schema`, `sample_values`, `validate_rule` (read-only). Its JSON output is schema-validated (unknown fields/rules are rejected → fallback). It has no approve/execute tool; the server refuses execution unless the version is `approved` and its dry run matches the current mapping hash. Approved versions are immutable; edits require a new version.
- **Idempotency:** target primary key is `id`; retries use `INSERT OR IGNORE` and report inserted vs skipped. Only one version may be live in the target at a time.

## Scope
Done: mapping proposal, risks, clarification questions, versioned plans, approval gate, dry run, quarantine with field-level evidence, execute/retry/rollback, reconciliation (counts + balance sum), history, tests, Docker/Render config.
Excluded (intentionally): authentication/multi-user, arbitrary transformation code, production DBs, live connectors, distributed execution, answering agent clarification questions inside the UI (answer by editing the mapping), streaming agent output.

## Tests
15 focused tests in `tests/test_server.py` cover rules, determinism, quarantine evidence, approval/execution gates, version locking, idempotent retry after partial failure, rollback, reconciliation, history, LLM tool loop (mocked), invalid-LLM-output fallback, and tool restriction.

## Limitations
No auth (anyone with the URL can reset state; use only with sample data). The live LLM path is covered by mocks in tests; it needs a real key to exercise end to end. Single-process SQLite.

## Deployment
Docker or Render (`render.yaml`): set `ANTHROPIC_API_KEY` in the host's secret settings, mount a disk at `/data`, health check `/healthz`.
**Status: not yet deployed.** The environment this repo was generated in has no network access, so no hosted URL exists. Deploy, then add the URL and reviewer notes here. Reviewers need no credentials: open the URL, click *Run agent*, review the plan, dry run, approve, execute.
