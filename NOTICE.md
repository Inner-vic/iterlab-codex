# Sources and licenses

This workbench is a separate project created at the user's request. Its native agent runtime is the installed official Codex App Server (or the company's EPT wrapper). It does not bundle or claim authorship of Codex.

The research exploration map was copied from the user's IterLab project `aideml-sdm`, including its frontend source, dependency lockfile, compiled assets, and dependency notices. It is an independent source copy, with no HTTP, database, or Python dependency on IterLab.

The original IterLab repository includes an MIT license attributed to Weco AI Ltd. That license is preserved in `LICENSE`. Third-party graph component licenses are preserved in `static/vendor/exploration-map.NOTICE.txt` and the linked legal comments file. `frontend/exploration-map/build.mjs` was modified to write only into this project's `static/vendor`.

Workbench layout and theme tokens are adapted from the user's IterLab interface. The native RPC client, local service, research data store, and UI integration are implemented in this project.
