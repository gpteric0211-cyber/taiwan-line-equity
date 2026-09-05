# Full Windows post-close deployment package

Build the current source, a consistent SQLite snapshot, Ollama, the registered
Qwen model, LINE launchers, and a self-contained Python folder into one Windows
deployment directory. Real `.env` files and credentials are always excluded.

Example build on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_full_post_close_windows.ps1 `
  -Clean `
  -PythonRuntimeDir "<trusted-full-python-runtime-folder>" `
  -CloudflaredExe "<trusted-cloudflared.exe>"
```

Output:

```text
dist\TaiwanStock_PostClose_Portable_Windows\
```

The Python runtime source must be a complete trusted Windows Python folder with
`python.exe`. The build copies the currently tested project `site-packages` into
that runtime and verifies imports before completing.

The model is copied from the existing content-addressed Ollama model store. The
model blob SHA-256 is verified against the Ollama manifest, avoiding a duplicate
copy of the original GGUF.

The SQLite database is exported with the SQLite backup API and checked with
`PRAGMA quick_check`; an actively used database is never copied byte-for-byte.
