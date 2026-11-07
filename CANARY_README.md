# Diagnostic Canary — synthetic data only

Not a production release. This branch is isolated from the repository's `main`.
The 40 audited R1.2 application files are unchanged; their checksums are in
`canary_audited_code.json`. The diagnostic entry point is `canary_app.py`.

The default URL starts a lightweight resource probe. The five accepted Search
modes run only at `?canary_ui=1`, after inspecting the actual Cloud budget.
`?canary_diagnostics=1` displays bounded telemetry. Missing cgroup metrics stay
unavailable; filesystem free space is not an application storage quota.

All bundled data is generated from seed 981205: 1,294,256 TOP-100 rows,
855,803 Portrait rows, and 152,363 synthetic market phrases. Product IDs start
at 900000000000; SKUs and models use SYNTHETIC namespaces. No corporate source
archives, finance, ads, inventory, user registries, tokens or audit evidence
are included. The projected SQLite members in the ZIPs are synthetic only.

No production Drive feed or Secrets is needed. No weekly hook is connected.
Caches are ephemeral and do not promise survival of a container restart.
Do not promote this branch or change an existing Streamlit app to use it.
