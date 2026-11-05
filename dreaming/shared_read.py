"""Read a shared file without blocking its writer's replace, and replace it without waiting on readers (Windows).

A writer that saves by writing a temporary file and renaming it over the old one fails on Windows while any reader
holds the old file open: a plain open() does not grant FILE_SHARE_DELETE, and os.replace (MoveFileEx) refuses to
replace a file that is open. With a large state file read by many hooks at once (the task checklist's state.json,
13 MB) the writer held its lock for 20 to 28 seconds at a time, retrying.

Both halves are needed:
- open_shared() opens for reading with share read, write and delete. The reader keeps the old, whole bytes.
- posix_replace() renames with FILE_RENAME_FLAG_POSIX_SEMANTICS, which succeeds while readers that opened with
  share-delete still hold the destination. A reader that used a plain open() still blocks it.
Off Windows, or on any failure, open_shared is a plain open and posix_replace returns False so the caller falls back
to os.replace.

    with open_shared(path) as fh:
        state = json.load(fh)

    if not posix_replace(tmp, path):
        os.replace(tmp, path)
"""
import io
import json
import os

_K32 = None


def _kernel32():
    global _K32
    if os.name != "nt":
        return None
    if _K32 is None:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        _K32 = k32
    return _K32


def open_shared(path, binary=False):
    """Open for reading with share read, write and delete; a plain open off Windows or on failure."""
    k32 = _kernel32()
    if k32 is not None:
        try:
            import ctypes
            import msvcrt
            h = k32.CreateFileW(os.path.abspath(path), 0x80000000, 7, None, 3, 0x80, None)   # GENERIC_READ, share RWD
            if h not in (None, ctypes.c_void_p(-1).value):
                raw = os.fdopen(msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY), "rb")
                return raw if binary else io.TextIOWrapper(raw, encoding="utf-8")
            if ctypes.get_last_error() in (2, 3):
                raise FileNotFoundError(2, "No such file", path)
        except FileNotFoundError:
            raise
        except Exception:
            pass
    return io.open(path, "rb") if binary else io.open(path, encoding="utf-8")


def posix_replace(src, dst):
    """Rename src over dst with POSIX semantics. True on success; False when the call is not available here (the
    caller then uses os.replace); PermissionError while a reader without share-delete holds dst, or while another
    process holds src."""
    k32 = _kernel32()
    if k32 is None:
        return False
    import ctypes
    from ctypes import wintypes
    h = k32.CreateFileW(os.path.abspath(src), 0x00010000 | 0x80000000, 7, None, 3, 0x80, None)   # DELETE|GENERIC_READ
    if h in (None, ctypes.c_void_p(-1).value):
        err = ctypes.get_last_error()
        if err in (5, 32):
            raise PermissionError(err, "source held", src)
        return False
    try:
        name = os.path.abspath(dst)

        class _RenameInfoEx(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE),
                        ("FileNameLength", wintypes.DWORD), ("FileName", ctypes.c_wchar * (len(name) + 1))]
        info = _RenameInfoEx(0x1 | 0x2, None, len(name) * 2, name)    # REPLACE_IF_EXISTS | POSIX_SEMANTICS
        if k32.SetFileInformationByHandle(h, 22, ctypes.byref(info), ctypes.sizeof(info)):   # FileRenameInfoEx
            return True
        err = ctypes.get_last_error()
    finally:
        k32.CloseHandle(h)
    if err in (5, 32):
        raise PermissionError(err, "destination held", dst)
    return False                                                   # e.g. a file system without POSIX rename


def load_json(path):
    with open_shared(path) as fh:
        return json.load(fh)
