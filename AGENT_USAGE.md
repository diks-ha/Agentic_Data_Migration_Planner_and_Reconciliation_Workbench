# AGENT_USAGE

## Two uses of AI
1. **Runtime agent (in the product):** Claude via the Messages API, tools `inspect_schema`, `sample_values`, `validate_rule` only. Output is a draft plan; humans approve. Every tool call is logged (`agent.tool_call`) and shown in the UI.
2. **Build assistant:** Claude generated this codebase from the brief.

## Representative prompts
- Build a migration planner/reconciliation workbench with agent mapping proposals, versioned plans, dry run, quarantine, idempotent execution, rollback and history (original brief).
- "enhance the ui make it interactive and beautiful"; "make it multipage not a single page app"; then the graded requirements (backend, persistence, tests, logs, docs, deployment).
- Runtime system prompt: see `SYSTEM` in `server.py`.

## Delegated to the assistant
Backend, SQLite schema, rule set, frontend pages, tests, Dockerfile, docs.

## Mistakes caught and fixes (real, from this build)
- Preview helper passed `{src, rule}` to a tool expecting `{field, rule}`; `/api/state` returned 500 and 6 tests failed. Fixed and covered by the suite.
- Sample data's duplicate-ID row also had an unknown country, so the duplicate-key rule was never exercised. Test caught it; sample data changed.
- First delivery was client-only (browser logic + localStorage), which did not meet the backend requirement; replaced by this architecture.
- Rejected design: letting the agent call approve/execute. Excluded by construction.
- **Not yet done by the author:** record observed behaviour of the live model (hallucinated rules, wrong mappings it proposed) after running with a real key. None is claimed here.

## Verification
`python3 -m unittest discover -s tests` (15 pass); `node --check static/app.js`. Not verified: browser rendering of the pages, the live Anthropic call, and the hosted deployment.
