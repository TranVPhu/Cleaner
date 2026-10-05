"""RAM: thống kê bộ nhớ, tiến trình ngốn RAM, thu gọn bộ nhớ và ứng dụng khởi động cùng Windows.

Chỉ dùng ctypes/winreg của Windows, không cần thư viện ngoài.
Các hàm thao tác ném RuntimeError kèm thông báo khi thất bại.
"""

import ctypes
import os
import struct
import subprocess
import time
import winreg
from ctypes import wintypes

from .engine import format_size

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel32.K32EmptyWorkingSet.argtypes = [wintypes.HANDLE]
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_ntdll.NtQuerySystemInformation.argtypes = [
    ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
_ntdll.NtQuerySystemInformation.restype = ctypes.c_long
_ntdll.NtSetSystemInformation.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong]
_ntdll.NtSetSystemInformation.restype = ctypes.c_long

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_SET_QUOTA = 0x0100

_WINDIR = os.path.normcase(os.environ.get("WINDIR", r"C:\Windows")) + os.sep


# ---------------------------------------------------------------- tổng quan RAM

class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def memory_info() -> dict:
    """{'total', 'avail', 'used', 'load' (%)}."""
    m = _MEMORYSTATUSEX(dwLength=ctypes.sizeof(_MEMORYSTATUSEX))
    _kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return {"total": m.ullTotalPhys, "avail": m.ullAvailPhys,
            "used": m.ullTotalPhys - m.ullAvailPhys, "load": m.dwMemoryLoad}


# ---------------------------------------------------------------- tiến trình

class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p)]


class _SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("NextEntryOffset", ctypes.c_ulong), ("NumberOfThreads", ctypes.c_ulong),
                ("WorkingSetPrivateSize", ctypes.c_longlong),
                ("HardFaultCount", ctypes.c_ulong), ("NumberOfThreadsHighWatermark", ctypes.c_ulong),
                ("CycleTime", ctypes.c_ulonglong), ("CreateTime", ctypes.c_longlong),
                ("UserTime", ctypes.c_longlong), ("KernelTime", ctypes.c_longlong),
                ("ImageName", _UNICODE_STRING), ("BasePriority", ctypes.c_long),
                ("UniqueProcessId", ctypes.c_void_p),
                ("InheritedFromUniqueProcessId", ctypes.c_void_p),
                ("HandleCount", ctypes.c_ulong), ("SessionId", ctypes.c_ulong),
                ("UniqueProcessKey", ctypes.c_void_p),
                ("PeakVirtualSize", ctypes.c_size_t), ("VirtualSize", ctypes.c_size_t),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t)]


def _raw_processes():
    """[(pid, tên, working set, private working set)] - đọc một lần cho mọi tiến trình,
    không cần mở từng tiến trình (giống Task Manager)."""
    size = 1 << 20
    while True:
        buf = ctypes.create_string_buffer(size)
        needed = ctypes.c_ulong()
        status = _ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(needed))
        if status == -0x3FFFFFFC:                    # STATUS_INFO_LENGTH_MISMATCH
            size = max(size * 2, needed.value + 65536)
            continue
        if status < 0:
            raise RuntimeError(f"Không đọc được danh sách tiến trình (mã {status & 0xFFFFFFFF:#x})")
        break

    out, offset = [], 0
    base = ctypes.addressof(buf)
    while True:
        info = _SYSTEM_PROCESS_INFORMATION.from_address(base + offset)
        pid = info.UniqueProcessId or 0
        if info.ImageName.Buffer:
            name = ctypes.wstring_at(info.ImageName.Buffer, info.ImageName.Length // 2)
        else:
            name = "System Idle Process" if pid == 0 else "System"
        if pid:
            out.append((pid, name, info.WorkingSetSize, info.WorkingSetPrivateSize))
        if not info.NextEntryOffset:
            return out
        offset += info.NextEntryOffset


def _image_path(pid):
    h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(len(buf))
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return None
    finally:
        _kernel32.CloseHandle(h)


def processes() -> list:
    """Tiến trình gộp theo tên, sắp theo RAM giảm dần.

    Mỗi phần tử: {'name', 'count', 'pids', 'ram' (private working set),
    'working_set', 'path', 'system'}. `system` = tiến trình của Windows hoặc
    không truy cập được -> không cho đóng.
    """
    me = os.getpid()
    groups = {}
    for pid, name, ws, private in _raw_processes():
        g = groups.setdefault(name.lower(), {"name": name, "count": 0, "pids": [], "ram": 0,
                                             "working_set": 0, "path": None})
        g["count"] += 1
        g["ram"] += private
        g["working_set"] += ws
        if pid != me:
            g["pids"].append(pid)
        if g["path"] is None:
            g["path"] = _image_path(pid)
    for g in groups.values():
        path = g["path"]
        g["system"] = path is None or os.path.normcase(path).startswith(_WINDIR)
    return sorted(groups.values(), key=lambda g: g["ram"], reverse=True)


def kill(groups) -> str:
    """Đóng (buộc tắt) mọi tiến trình trong các nhóm đã chọn, kèm tiến trình con."""
    before = memory_info()["avail"]
    pids = [pid for g in groups for pid in g["pids"]]
    if not pids:
        return "Không có tiến trình nào để đóng."
    args = ["taskkill.exe", "/F", "/T"]
    for pid in pids:
        args += ["/PID", str(pid)]
    try:
        subprocess.run(args, capture_output=True, creationflags=_NO_WINDOW, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"Không chạy được taskkill: {e}") from e
    time.sleep(1)                                    # chờ Windows thu hồi bộ nhớ
    alive = {pid for pid, *_ in _raw_processes()}
    left = [g["name"] for g in groups if any(p in alive for p in g["pids"])]
    freed = memory_info()["avail"] - before
    msg = f"Đã đóng: {', '.join(g['name'] for g in groups if g['name'] not in left) or '(không có)'}."
    if freed > 0:
        msg += f"\nRAM trống thêm khoảng {format_size(freed)}."
    if left:
        msg += ("\n\nKhông đóng được (bị bảo vệ hoặc cần quyền Admin): " + ", ".join(left))
    return msg


# ---------------------------------------------------------------- thu gọn bộ nhớ

def trim_working_sets() -> str:
    """Yêu cầu mọi tiến trình trả lại phần RAM không dùng tới (EmptyWorkingSet).

    Hiệu quả tức thời nhưng tạm thời: ứng dụng sẽ nạp lại khi cần.
    """
    before = memory_info()["avail"]
    done = 0
    for pid, *_ in _raw_processes():
        h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_SET_QUOTA,
                                  False, pid)
        if not h:
            continue
        try:
            done += bool(_kernel32.K32EmptyWorkingSet(h))
        finally:
            _kernel32.CloseHandle(h)
    time.sleep(0.5)
    freed = memory_info()["avail"] - before
    return (f"Đã thu gọn bộ nhớ của {done} tiến trình.\n"
            f"RAM trống thêm khoảng {format_size(max(freed, 0))}.\n\n"
            "Lưu ý: đây là giải pháp tạm thời - muốn RAM trống lâu dài, "
            "hãy đóng bớt ứng dụng nặng hoặc tắt chúng khỏi danh sách khởi động.")


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Luid", _LUID),
                ("Attributes", wintypes.DWORD)]


def _enable_privilege(name):
    TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY, SE_PRIVILEGE_ENABLED = 0x20, 0x8, 0x2
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(_kernel32.GetCurrentProcess(),
                                      TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(token)):
        raise RuntimeError("Không mở được token của tiến trình.")
    try:
        tp = _TOKEN_PRIVILEGES(PrivilegeCount=1, Attributes=SE_PRIVILEGE_ENABLED)
        if not _advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(tp.Luid)):
            raise RuntimeError(f"Không tìm thấy quyền {name}.")
        _advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        if ctypes.get_last_error() != 0:             # ERROR_NOT_ALL_ASSIGNED
            raise RuntimeError("Cần chạy với quyền Admin để xoá bộ nhớ chờ.")
    finally:
        _kernel32.CloseHandle(token)


def purge_standby() -> str:
    """Xoá danh sách bộ nhớ chờ (standby list) - cần quyền Admin."""
    _enable_privilege("SeProfileSingleProcessPrivilege")
    before = memory_info()["avail"]
    command = ctypes.c_int(4)                        # MemoryPurgeStandbyList
    status = _ntdll.NtSetSystemInformation(80, ctypes.byref(command), ctypes.sizeof(command))
    if status < 0:
        raise RuntimeError(f"Windows từ chối xoá bộ nhớ chờ (mã {status & 0xFFFFFFFF:#x}).")
    return ("Đã xoá bộ nhớ chờ (cache file của Windows).\n"
            f"Thay đổi RAM trống: {format_size(max(memory_info()['avail'] - before, 0))}.\n\n"
            "Bộ nhớ chờ vốn được Windows nhường lại ngay khi ứng dụng cần, nên thao tác này "
            "chủ yếu hữu ích trước khi chạy một ứng dụng/game rất nặng.")


# ---------------------------------------------------------------- khởi động cùng Windows

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
_HIVES = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}


def _approved_enabled(hive, sub, name):
    """Task Manager lưu trạng thái bật/tắt ở StartupApproved: byte đầu lẻ = đã tắt."""
    try:
        with winreg.OpenKey(_HIVES[hive], rf"{_APPROVED}\{sub}") as k:
            data, _ = winreg.QueryValueEx(k, name)
    except OSError:
        return True
    return not (isinstance(data, bytes) and data and data[0] & 1)


def startup_items() -> list:
    """Ứng dụng khởi động cùng Windows (giống tab Startup apps của Task Manager).

    Mỗi phần tử: {'name', 'command', 'where', 'enabled', 'hive', 'approved'}.
    """
    items = []
    sources = (("HKCU", _RUN, "Run", "Người dùng"),
               ("HKLM", _RUN, "Run", "Mọi người dùng"),
               ("HKLM", _RUN32, "Run32", "Mọi người dùng"))
    for hive, key, approved, where in sources:
        try:
            with winreg.OpenKey(_HIVES[hive], key, 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    if name and isinstance(value, str):
                        items.append({"name": name, "command": value, "where": where,
                                      "hive": hive, "approved": approved})
        except OSError:
            continue

    folders = (("HKCU", os.path.join(os.environ.get("APPDATA", ""),
                                     r"Microsoft\Windows\Start Menu\Programs\Startup"), "Người dùng"),
               ("HKLM", os.path.join(os.environ.get("PROGRAMDATA", ""),
                                     r"Microsoft\Windows\Start Menu\Programs\Startup"), "Mọi người dùng"))
    for hive, folder, where in folders:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if name.lower() == "desktop.ini":
                continue
            items.append({"name": os.path.splitext(name)[0], "value": name,
                          "command": os.path.join(folder, name), "where": where + " (thư mục Startup)",
                          "hive": hive, "approved": "StartupFolder"})

    for it in items:
        it.setdefault("value", it["name"])
        it["enabled"] = _approved_enabled(it["hive"], it["approved"], it["value"])
    return sorted(items, key=lambda it: it["name"].lower())


def set_startup(item, enabled: bool):
    """Bật/tắt một mục khởi động theo đúng cách Task Manager làm (không xoá mục gốc)."""
    if enabled:
        data = b"\x02" + b"\x00" * 11
    else:
        data = b"\x03\x00\x00\x00" + struct.pack("<Q", int((time.time() + 11644473600) * 10**7))
    try:
        with winreg.CreateKeyEx(_HIVES[item["hive"]], rf"{_APPROVED}\{item['approved']}", 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, item["value"], 0, winreg.REG_BINARY, data)
    except PermissionError as e:
        raise RuntimeError(f"\"{item['name']}\" áp dụng cho mọi người dùng - "
                           "cần chạy với quyền Admin để thay đổi.") from e
