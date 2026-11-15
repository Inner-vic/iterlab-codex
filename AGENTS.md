# IterLab Codex workbench

- This is an independent prototype. Never import `../aideml-sdm` at runtime, connect to its HTTP service, or open/migrate its databases or credentials.
- Codex App Server owns the agent loop, tools, sandbox, approvals, and semantic compaction. The application owns research records, projections, UI, and durable delivery; do not introduce a second LLM decision loop.
- Keep projects, records, source events, and pending/unknown deliveries durable. An RPC timeout is not proof that a turn was rejected; never automatically resend an uncertain request.
- Python scripts/tests on Windows run in the foreground through `scripts/run-quiet.ps1`, using a real Python executable; propagate the exact exit code. Child application processes use CREATE_NO_WINDOW. Managed application services are permitted; tests must clean up their own processes.
- The original IterLab service on 8769 is out of scope. This project's local development port is 8781 and its independent persistent root is `.data` (override with ITERLAB_CODEX_DATA).
- Never copy credentials. Codex and EPT use their own official login/configuration. Never switch channels as a failure fallback.
- Use `scripts/storage.py snapshot` for compressed operational backups and `scripts/snapshot.py` for idle-only deployment backups. Snapshot scope is SQLite/settings; workspace artifacts remain separately managed. Verify/restore into a new directory before replacing data. Never silently delete source events, projects, conversation records, artifacts or pending identities to meet a disk budget.
- Keep regression fixtures small and close their SQLite connections. A production-data replay must be necessary and bounded; reuse a verified baseline instead of leaving a complete production database copy for every UI check. Remove owned temporary test data after testing. Cold backup retention and test cleanup must use an explicit inventory and preserve selected recovery points and acceptance reports.
