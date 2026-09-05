# Taiwan50 Dashboard Portable

This folder-based portable package runs the Taiwan50 dashboard with a bundled
Python virtual environment. It does not build an exe.

Two portable styles are supported:

- venv-based portable: includes `.venv`. This is convenient for the same or a
  very similar machine, but `pyvenv.cfg` may still reference the Python used
  during the build.
- true self-contained portable: includes `python\python.exe` on Windows or
  `python/bin/python` on macOS/Linux. This is the preferred style for machines
  where Python is not installed.

## Windows

Build on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable_windows.ps1 -Clean
```

Output:

```text
dist/Taiwan50Dashboard_Portable_Windows/
```

Run:

```text
啟動台股分析系統.bat
```

## macOS / Linux

Build on macOS or Linux:

```sh
sh packaging/build_portable_macos.sh --clean
```

Output:

```text
dist/Taiwan50Dashboard_Portable_Mac/
```

Run:

```sh
./啟動台股分析系統.sh
```

The macOS build should be created on the target platform. Apple Silicon and
Intel Python virtual environments are not guaranteed to be interchangeable.

## Data And Secrets

By default, build scripts do not copy local DB files or `.env` files.

Windows optional switches:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable_windows.ps1 -IncludeDb -IncludeEnv
```

Only use those switches when you intentionally want local data or local
environment values inside the portable folder.

## Embedded Python

Windows true self-contained examples:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable_windows.ps1 -Clean -UseEmbeddedPython -PythonRuntimeDir "<path-to-python-runtime>"
```

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable_windows.ps1 -Clean -UseEmbeddedPython -PythonEmbedZip ".\python-runtime.zip"
```

The build script never downloads Python. Provide a trusted Python runtime folder
or zip yourself. If `python\python.exe` is present, portable startup uses it
before `.venv`.

If the startup screen shows an executable outside the configured portable runtime
while running from the portable folder, the launcher is not using the portable
Python environment. Rebuild the portable package and run the launcher inside the
`dist/Taiwan50Dashboard_Portable_Windows` folder.

The current venv-based portable package may not contain `python\python.exe`.
That is normal. The launcher tries `python\python.exe` first when it exists,
then falls through to `.venv\Scripts\python.exe`. In portable mode it does not
fall back to system Python.

Expected portable startup Python:

```text
.venv\Scripts\python.exe
```

or:

```text
python\python.exe
```

## Runtime

The runtime launcher starts:

```text
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

The launcher runs that command with the app working directory set to
`app\review_src` on Windows portable packages, or `app/review_src` on
macOS/Linux portable packages. The project imports `api`, `adapter`, and `core`
as top-level packages, so `review_src` must be the working directory.

If startup fails with:

```text
ModuleNotFoundError: No module named 'api'
```

the package was built with an old launcher or an incorrect working directory.
Rebuild the portable package with the current launcher.

The launcher uses `python\python.exe` / `python/bin/python` when present,
otherwise the Python inside `.venv`. Runtime startup does not download Python or
packages; packages are installed only during the build step.
