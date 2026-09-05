# Embedded Python Portable Runtime

This project supports two portable package styles.

## venv-based portable

The default Windows build creates:

```text
dist/Taiwan50Dashboard_Portable_Windows/.venv/
```

This includes packages such as `uvicorn` and `fastapi`, but the generated
`.venv/pyvenv.cfg` can reference the Python executable used during build.
That means it is portable enough for similar machines, but it is not a fully
self-contained Python runtime.

## true self-contained portable

A true self-contained Windows build contains:

```text
dist/Taiwan50Dashboard_Portable_Windows/python/python.exe
```

Build examples:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable_windows.ps1 -Clean -UseEmbeddedPython -PythonRuntimeDir "<path-to-python-runtime>"
```

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable_windows.ps1 -Clean -UseEmbeddedPython -PythonEmbedZip ".\python-runtime.zip"
```

The build script does not download Python. The runtime folder or zip must be
provided by the operator.

## Runtime rule

Portable startup never falls back to system Python. In portable mode, the
launcher accepts only:

```text
python\python.exe
.venv\Scripts\python.exe
```

On macOS/Linux, the accepted portable paths are:

```text
python/bin/python
.venv/bin/python
```

If neither runtime can import `uvicorn` and `fastapi`, startup stops with:

```text
Portable Python environment not found or incomplete.
Please rebuild the portable package.
```
