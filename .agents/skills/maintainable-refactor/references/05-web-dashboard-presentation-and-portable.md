# Output Sanitization, Web Presentation, And Portable Build Rules

The first section is the single canonical policy for every normal API, web, and LINE user-facing surface. The later web/portable sections apply only when those deliverables are in scope.

## Canonical Output Sanitization And End-User Display Policy

Keep full provider/source/date/confidence provenance in internal models, DB rows, audit reports, and authorized diagnostics. Before data reaches a normal user-facing API response, web page, text reply, Flex Message, or push alert:

- Convert numpy/pandas scalars, `pandas.Timestamp`, `datetime`, and `date` to supported JSON/display values.
- Convert or reject NaN, Infinity, exceptions, tracebacks, and non-serializable object representations.
- Never expose secrets, filesystem/DB paths, raw SQL, internal table/field names, exception details, or repair/queue internals.
- Do not show raw implementation/provider tokens such as `provider`, `source`, `Fugle`, `TWSE`, `TPEX`, `TDCC`, `FinMind`, `numpy.float`, `numpy.int`, `object at`, `KeyError`, `Traceback`, `NoneType`, `no_local_source_rows`, or `missing source row` on normal end-user surfaces.
- Use a neutral, human-readable provenance phrase when needed, such as `官方盤後資料`, while preserving exact source identifiers internally for traceability.
- Do not implement this as a naive substring filter that damages legitimate explanatory sentences. Sanitize typed fields at the output boundary and test exact rendered payloads.
- Authorized, access-controlled debug/audit tools may expose source metadata needed for diagnosis, but never secrets or raw personal identifiers.

Preferred fallback text:

- `資料補齊中`
- `技術資料補齊中`
- `資料補齊後顯示`
- `成本資料補齊後顯示`
- `此市場資料尚未完整支援，暫不顯示半成品分析`

This is the only complete definition of the raw-internals rule. Other references must link here instead of copying the list.

## Mobile-First Web Dashboard

- Prioritize mobile browser readability; desktop is secondary.
- Prefer short cards, compact labels, wrapped text, and a single-column flow on small screens.
- Avoid wide tables when a concise conclusion is clearer.
- Do not duplicate data already shown elsewhere.
- Watchlist may show realtime summaries during market hours.
- Taiwan50 remains after-hours/close-batch and must not consume Fugle realtime quota.
- If the web dashboard is retired, confirm whether its internal/portable tooling is also retired before deleting these rules.

## Cross-Platform Portable Requirements

- Support Windows launchers with `.bat`/`.cmd` and macOS/Linux launchers with `.sh`.
- Keep portable builds folder-based, not executable bundles, unless the user opens a separate explicit phase.
- Build Windows packages on Windows and Mac packages on Mac; do not share Python runtimes or virtual environments between platforms.
- Runtime startup must not download packages. Dependency/runtime installation belongs to the build step.
- Portable mode must not fall back to system Python.
- A truly self-contained package requires a bundled `python/` runtime; a venv alone may not work on a machine without a compatible Python installation.
- Build scripts may accept configurable `PythonRuntimeDir` or `PythonEmbedZip` inputs but must not download Python themselves.
- Portable launchers must use paths relative to their own directory and must not hard-code a developer machine path.
- Build scripts must not change firewall, antivirus, registry, or system-security settings.
- Keep launchers and build scripts transparent and inspectable.
- Run uvicorn with cwd `review_src` and module `app:app`. In portable packages use cwd `app/review_src`.
- Do not use `review_src.app:app` until top-level imports have been converted to package-relative imports.
- Try the bundled `python` runtime first, then the bundled `.venv`; absence of bundled `python` alone is not an error when the bundled venv is valid.
- Do not commit `dist/`, `portable/`, `Taiwan50Dashboard_Portable_*/`, generated packages, runtimes, local DBs, `.env`, tokens, or secrets.
- Include DB/secrets only through an explicit, reviewed build flag.
