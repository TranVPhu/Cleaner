"""Quét và xoá file rác.

Nguyên tắc an toàn:
- Không bao giờ đi theo / xoá symlink, junction (reparse point).
- Chỉ xoá file bên trong thư mục gốc của hạng mục, không xoá chính thư mục gốc.
- Từ chối thư mục gốc nằm trong danh sách được bảo vệ (C:\\, Windows, System32...).
- File đang bị khoá hoặc không có quyền thì bỏ qua.
"""

import ctypes
import fnmatch
import glob
import os
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .categories import Category

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass
class ScanResult:
    category: Category
    roots: list = field(default_factory=list)
    files: list = field(default_factory=list)   # [(path, size)]
    size: int = 0
    count: int = 0
    cutoff: float = 0                            # timestamp; 0 = không giới hạn tuổi


@dataclass
class CleanResult:
    freed: int = 0
    deleted: int = 0
    failed: int = 0


# ---------------------------------------------------------------- tiện ích

def format_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def running_processes() -> set:
    """Tên (chữ thường) các tiến trình đang chạy."""
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.split('","')[0].strip('"').lower() for line in out.splitlines() if line}


def _protected_dirs() -> set:
    env = os.environ
    dirs = {
        env.get("SystemDrive", "C:") + "\\",
        env.get("WINDIR", r"C:\Windows"),
        os.path.join(env.get("WINDIR", r"C:\Windows"), "System32"),
        env.get("USERPROFILE", ""), env.get("APPDATA", ""), env.get("LOCALAPPDATA", ""),
        env.get("PROGRAMDATA", ""), env.get("ProgramFiles", ""),
        env.get("ProgramFiles(x86)", ""),
    }
    return {os.path.normcase(os.path.normpath(d)) for d in dirs if d}


def _is_reparse(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _entry_kind(entry: os.DirEntry):
    """Trả về 'dir', 'file' hoặc None (link / lỗi -> bỏ qua)."""
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return None, None
    if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & _REPARSE:
        return None, st
    if stat.S_ISDIR(st.st_mode):
        return "dir", st
    return "file", st


def resolve_roots(cat: Category) -> list:
    protected = _protected_dirs()
    roots = set()
    for raw in cat.paths:
        pattern = os.path.expandvars(raw)
        if "%" in pattern:              # biến môi trường không tồn tại
            continue
        for p in glob.glob(pattern):
            p = os.path.normpath(p)
            if (os.path.isdir(p) and not _is_reparse(p)
                    and len(Path(p).parts) >= 3
                    and os.path.normcase(p) not in protected):
                roots.add(p)
    return sorted(roots)


def _version_key(name: str):
    try:
        return tuple(int(part) for part in name.split("."))
    except ValueError:
        return None


def _old_version_roots(cat: Category) -> list:
    """Các thư mục phiên bản cũ (mọi bản trừ bản có số phiên bản cao nhất)."""
    roots = []
    for parent in resolve_roots(cat):
        versions = []
        try:
            with os.scandir(parent) as it:
                for e in it:
                    key = _version_key(e.name)
                    if key and _entry_kind(e)[0] == "dir":
                        versions.append((key, e.path))
        except OSError:
            continue
        versions.sort()
        roots += [path for _, path in versions[:-1]]
    return roots


def _iter_files(root: str, pattern: str, recursive: bool):
    stack = [root]
    pattern = pattern.lower()
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError:
            continue
        with it:
            for entry in it:
                kind, st = _entry_kind(entry)
                if kind == "dir" and recursive:
                    stack.append(entry.path)
                elif kind == "file" and fnmatch.fnmatch(entry.name.lower(), pattern):
                    yield entry.path, st


# ---------------------------------------------------------------- thùng rác

class _SHQUERYRBINFO(ctypes.Structure):
    if ctypes.sizeof(ctypes.c_void_p) == 4:   # shellapi.h dùng pack(1) trên Win32
        _pack_ = 1
    _fields_ = [("cbSize", ctypes.c_uint32),
                ("i64Size", ctypes.c_int64),
                ("i64NumItems", ctypes.c_int64)]


def _query_recycle_bin():
    info = _SHQUERYRBINFO(cbSize=ctypes.sizeof(_SHQUERYRBINFO))
    try:
        hr = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    except (AttributeError, OSError):
        return 0, 0
    return (info.i64Size, info.i64NumItems) if hr == 0 else (0, 0)


def _empty_recycle_bin() -> bool:
    SHERB_NOCONFIRMATION, SHERB_NOPROGRESSUI, SHERB_NOSOUND = 1, 2, 4
    flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    try:
        return ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags) == 0
    except (AttributeError, OSError):
        return False


# ---------------------------------------------------------------- quét / dọn

def scan(cat: Category) -> ScanResult:
    result = ScanResult(category=cat)
    if cat.kind == "recycle_bin":
        result.size, result.count = _query_recycle_bin()
        return result

    result.cutoff = time.time() - cat.min_age_hours * 3600 if cat.min_age_hours else 0
    result.roots = _old_version_roots(cat) if cat.kind == "old_versions" else resolve_roots(cat)
    for root in result.roots:
        for path, st in _iter_files(root, cat.pattern, cat.recursive):
            if result.cutoff and st.st_mtime > result.cutoff:
                continue
            result.files.append((path, st.st_size))
            result.size += st.st_size
    result.count = len(result.files)
    return result


def _delete_file(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except PermissionError:
        try:                            # file chỉ-đọc
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
            return True
        except OSError:
            return False
    except OSError:
        return False


def _prune_empty_dirs(directory: str, cutoff: float, is_root: bool = True):
    """Xoá thư mục con rỗng (không xoá thư mục gốc, không đụng vào link)."""
    try:
        with os.scandir(directory) as it:
            subdirs = [e.path for e in it if _entry_kind(e)[0] == "dir"]
    except OSError:
        return
    for sub in subdirs:
        _prune_empty_dirs(sub, cutoff, is_root=False)
    if is_root:
        return
    try:
        if cutoff and os.stat(directory).st_ctime > cutoff:   # thư mục mới tạo
            return
        os.rmdir(directory)             # chỉ thành công khi rỗng
    except OSError:
        pass


def clean(result: ScanResult, on_progress=None) -> CleanResult:
    out = CleanResult()
    cat = result.category
    if cat.kind == "recycle_bin":
        if result.count and _empty_recycle_bin():
            out.freed, out.deleted = result.size, result.count
        elif result.count:
            out.failed = result.count
        return out

    total = len(result.files) or 1
    for i, (path, size) in enumerate(result.files):
        if _delete_file(path):
            out.freed += size
            out.deleted += 1
        else:
            out.failed += 1
        if on_progress and i % 200 == 0:
            on_progress(i / total)

    if cat.recursive:
        # Thư mục phiên bản cũ thì xoá luôn cả thư mục gốc (nếu đã rỗng).
        is_root = cat.kind != "old_versions"
        for root in result.roots:
            _prune_empty_dirs(root, result.cutoff, is_root=is_root)
    return out
