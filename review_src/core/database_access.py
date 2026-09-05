"""Cooperative connection leases for replacing the local market DB safely.

SQLite's own transaction locks still arbitrate writes. This separate file lock
prevents replacing a database while any participating connection/request uses it.
The lock file must remain in place; its existence does not mean it is locked.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
import sqlite3
import time

if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class _Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _kernel.LockFileEx.restype = wintypes.BOOL
    _kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _kernel.UnlockFileEx.restype = wintypes.BOOL


class DatabaseBusyError(TimeoutError):
    pass


class DatabaseLease:
    def __init__(self, database: Path, *, exclusive=False, timeout=None):
        self.path = Path(str(database.resolve()) + ".access.lock")
        self.exclusive = exclusive
        self.timeout = max(0, float(timeout if timeout is not None else
                                   os.getenv("EQUITY_DB_ACCESS_TIMEOUT_SECONDS", "180")))
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                if os.name == "nt":
                    self.overlapped = _Overlapped()
                    self.handle = msvcrt.get_osfhandle(self.stream.fileno())
                    acquired = _kernel.LockFileEx(self.handle, 1 | (2 if self.exclusive else 0),
                                                  0, 1, 0, ctypes.byref(self.overlapped))
                    if not acquired and ctypes.get_last_error() != 33:
                        raise ctypes.WinError(ctypes.get_last_error())
                else:
                    import fcntl

                    try:
                        fcntl.flock(self.stream.fileno(), fcntl.LOCK_NB |
                                    (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH))
                        acquired = True
                    except OSError as exc:
                        if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                            raise
                        acquired = False
                if acquired:
                    return self
                if time.monotonic() >= deadline:
                    raise DatabaseBusyError("Market database publication is busy; retry shortly")
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        except BaseException:
            self.stream.close()
            self.stream = None
            raise

    def close(self):
        if self.stream is not None:
            try:
                if os.name == "nt":
                    _kernel.UnlockFileEx(self.handle, 0, 1, 0, ctypes.byref(self.overlapped))
            finally:
                self.stream.close()
                self.stream = None

    __enter__ = acquire

    def __exit__(self, *_args):
        self.close()


class _LeasedConnection(sqlite3.Connection):
    _lease = None

    def close(self):
        try:
            super().close()
        finally:
            if self._lease is not None:
                self._lease.close()
                self._lease = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass  # Connection construction may have failed before SQLite opened it.


def connect(database: Path, *, readonly=False, **options):
    target = database.resolve()
    if readonly and not target.is_file():
        raise sqlite3.OperationalError("unable to open database file")
    lease = DatabaseLease(target).acquire()
    try:
        conn = sqlite3.connect(target.as_uri() + "?mode=ro" if readonly else target,
                               uri=readonly, factory=_LeasedConnection, **options)
        conn._lease = lease
        return conn
    except BaseException:
        lease.close()
        raise
