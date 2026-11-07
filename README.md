# O_STIN_WB_ENTRY_POINTS

Standalone read-only Entry Points application. This repository contains code
only: no WB finance engine, user databases, RAW files, prepared datasets or keys.

Streamlit entrypoint: **app.py**. Python 3.13. Dependencies are pinned in
requirements.txt. Intended repository: bmargarita91-del/O_STIN_WB_ENTRY_POINTS,
PUBLIC, feeding a separate PUBLIC Streamlit app. The existing WB repository
and app stay PRIVATE and unchanged. No second-private-app quota is required.
Repository/app creation and publishing require separate approval.

After public-scope approval and audit, the new app needs one configuration:

```toml
entry_index_url = "https://drive.google.com/uc?export=download&id=APPROVED_ENTRY_INDEX_FILE_ID"
```

This is a public data URL, not a credential. Do not copy the private WB app's
Secrets or service-account JSON here. Only an explicitly approved derived feed
is supported; the private WB snapshot format is rejected.

For local WORK preview, set ENTRY_DATA_ROOT to the separate prepared review
folder and ENTRY_PREVIEW=1. No source is bundled as an empty/demo replacement.

Weekly data updates use the separately installed, approved post-import hook
after WB's existing Search importer. They do not require Git pushes or changes
to app configuration. Nothing is uploaded from this application; visitors
cannot edit master data. Public data is downloadable and must be reviewed as
public in full, not merely as the currently displayed filtered table.
