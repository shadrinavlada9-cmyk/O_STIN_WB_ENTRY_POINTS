# O_STIN_WB_ENTRY_POINTS - PUBLIC SEARCH R2 candidate

CORRECTION R1: shared checked XLSX writer splits after 1,048,575 data rows,
verifies written counts and preserves all columns/values. Damaged cache
manifests trigger bounded immutable-generation repair. Future local feed
preparation has a fixed source/public-field allowlist; unexpected fields
require architectural approval, never automatic publication. RELEASE HOLD.
Existing project: shadrinavlada9-cmyk/O_STIN_WB_ENTRY_POINTS.
WORK candidate only: no push, deployment or data publication is authorized.
Entrypoint app.py, Python3.13, pinned requirements.txt.

Five routes: Entry Points; all O'STIN queries (brand analysis is a submode);
WB category-wide query totals; products requiring attention; market growth signals.
The market report is NOT O'STIN orders or finance.

Data remains outside Git. entry_index_url is the existing Entry Points URL.
search_index_url is the proposed NEW approved Search index. No keys or private
WB snapshots are used. These are public URLs, not credentials. The new feed
must be independently approved and published before configuring Cloud.

Each table has a full-table XLSX download, each mode a multi-sheet workbook.
Full raw available rows use streamed CSV/ZIP without pagination/TOP truncation.
Only source TOP100 coverage is available for O'STIN; no invented full market share.
No financial, ad-cost, stock or user-registry data is included.

Local preview variables: ENTRY_DATA_ROOT, ENTRY_CACHE_ROOT, ENTRY_PREVIEW=1,
PUBLIC_SEARCH_ROOT (prepared local feed), PUBLIC_SEARCH_CACHE (isolated cache).
Do not commit those local paths/configuration. .gitignore is default-deny.
Weekly preparation is separate from the private 01 button; no hook is installed.

CORRECTION R1.1 (WORK, RELEASE HOLD): use Prepare Excel, then Download Excel.
Preparation runs in a bounded background queue and atomically publishes a
SHA256-verified disk artifact. The download serves ready bytes without running
the generator or changing Streamlit's client timeout. Identical inputs share
one job. The last job can be reopened via the URL after a page reload; filters
inside its workbook describe that saved slice, not necessarily current UI filters.

PUBLIC_XLSX_CACHE optionally selects the Excel-only cache directory. Default:
inside the public Search runtime cache/temp directory. One global producer,
up to three queued/active requests per worker; cleanup threshold 2 GiB, workbook
limit 512 MiB, hot immutable-byte cache 192 MiB. Active reads are protected.
The cache is temporary: a container restart may remove it, requiring preparation
again from the same approved public sources. No credentials, financial databases
or user registries belong in this cache or repository.
