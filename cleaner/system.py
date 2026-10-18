"""Tối ưu hệ thống: hiberfil.sys, pagefile.sys, ổ ảo WSL, giới hạn RAM WSL, WinSxS.

Các hàm `set_*` / `wsl_*` / `winsxs_cleanup` chạy lâu và phần lớn cần quyền Admin;
chúng ném RuntimeError kèm thông báo khi thất bại.
"""

import ctypes
import json
import os
import re
import subprocess
import tempfile
import time
import winreg

from .engine import format_size

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_MEMORY_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
_LXSS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Lxss"

SYSTEM_DRIVE = os.environ.get("SystemDrive", "C:")


def _run(args, encoding="oem", env=None, timeout=None):
    try:
        p = subprocess.run(args, capture_output=True, creationflags=_NO_WINDOW,
                           env=env, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"Không chạy được {args[0]}: {e}") from e
    out = (p.stdout + p.stderr).decode(encoding, errors="replace").replace("\x00", "").strip()
    if p.returncode != 0:
        raise RuntimeError(out or f"{args[0]} lỗi (mã {p.returncode})")
    return out


def _wsl(*args, timeout=None):
    return _run(["wsl.exe", *args], encoding="utf-8",
                env={**os.environ, "WSL_UTF8": "1"}, timeout=timeout)


def _root_file_size(name: str) -> int:
    """Kích thước file ở gốc ổ hệ thống (đọc từ danh sách thư mục, không cần mở file)."""
    try:
        with os.scandir(SYSTEM_DRIVE + "\\") as it:
            for e in it:
                if e.name.lower() == name:
                    return e.stat(follow_symlinks=False).st_size
    except OSError:
        pass
    return 0


def ram_bytes() -> int:
    kb = ctypes.c_ulonglong()
    if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(kb)):
        return kb.value * 1024
    return 0


# ---------------------------------------------------------------- hibernation

def hibernation_info() -> dict:
    size = _root_file_size("hiberfil.sys")
    ram = ram_bytes()
    if not size:
        mode = "off"
    elif ram and size < ram * 0.3:      # reduced ≈ 20% RAM, full ≈ 40% RAM
        mode = "reduced"
    else:
        mode = "full"
    return {"size": size, "mode": mode}


def set_hibernation(mode: str) -> str:
    """mode: 'off' | 'reduced' (chỉ giữ Fast Startup) | 'full'."""
    if mode == "off":
        _run(["powercfg.exe", "/h", "off"])
        return "Đã tắt Hibernate và Fast Startup. hiberfil.sys đã được xoá."
    _run(["powercfg.exe", "/h", "on"])
    _run(["powercfg.exe", "/h", "/type", mode])
    if mode == "reduced":
        return "hiberfil.sys đã được rút gọn (vẫn giữ Fast Startup, không còn Hibernate)."
    return "Đã bật lại Hibernate đầy đủ."


# ---------------------------------------------------------------- pagefile

def pagefile_info() -> dict:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _MEMORY_KEY) as k:
            entries = winreg.QueryValueEx(k, "PagingFiles")[0]
    except OSError:
        entries = []
    auto = any(e.strip().startswith("?:") for e in entries)
    custom_mb = None
    for e in entries:
        parts = e.split()
        if (len(parts) >= 3 and parts[0].lower().startswith(SYSTEM_DRIVE.lower())
                and parts[2].isdigit() and int(parts[2]) > 0):
            custom_mb = int(parts[2])
    return {"size": _root_file_size("pagefile.sys"), "auto": auto,
            "custom_mb": custom_mb, "ram": ram_bytes()}


def set_pagefile(size_mb) -> str:
    """size_mb=None: để Windows tự quản lý; ngược lại đặt cố định trên ổ hệ thống."""
    if size_mb is None:
        script = ("$cs = Get-CimInstance Win32_ComputerSystem; "
                  "Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile=$true}")
    else:
        name = f"{SYSTEM_DRIVE}\\pagefile.sys"
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "$cs = Get-CimInstance Win32_ComputerSystem; "
            "Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile=$false}; "
            f"$pf = Get-CimInstance Win32_PageFileSetting | Where-Object Name -like '{SYSTEM_DRIVE}*'; "
            f"if (-not $pf) {{ $pf = New-CimInstance -ClassName Win32_PageFileSetting "
            f"-Property @{{Name='{name}'}} }}; "
            f"Set-CimInstance -InputObject $pf -Property @{{InitialSize=[uint32]{size_mb}; "
            f"MaximumSize=[uint32]{size_mb}}}"
        )
    _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script])
    what = ("Windows tự quản lý" if size_mb is None
            else f"cố định {format_size(size_mb * 2**20)}")
    return f"Đã đặt pagefile: {what}.\nCần khởi động lại máy để có hiệu lực."


# ---------------------------------------------------------------- WSL

def wsl_distros() -> list:
    distros = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _LXSS_KEY)
    except OSError:
        return distros
    with root:
        i = 0
        while True:
            try:
                guid = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, guid) as k:
                    name = winreg.QueryValueEx(k, "DistributionName")[0]
                    base = winreg.QueryValueEx(k, "BasePath")[0]
                    try:
                        vhd = winreg.QueryValueEx(k, "VhdFileName")[0]
                    except OSError:
                        vhd = "ext4.vhdx"
            except OSError:
                continue
            if base.startswith("\\\\?\\"):
                base = base[4:]
            path = os.path.join(base, vhd)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            distros.append({"name": name, "vhd": path, "size": size,
                            "docker": name.lower().startswith("docker-desktop")})
    return distros


def wsl_compact(distro: dict) -> str:
    """Dọn apt cache + TRIM bên trong distro, tắt WSL rồi thu gọn file .vhdx bằng diskpart."""
    before = distro["size"]
    try:
        _wsl("-d", distro["name"], "-u", "root", "--", "sh", "-c",
             "apt-get clean >/dev/null 2>&1; fstrim -a >/dev/null 2>&1; true", timeout=900)
    except RuntimeError:
        pass                            # không bắt buộc
    _wsl("--shutdown", timeout=120)

    fd, script = tempfile.mkstemp(suffix=".txt", text=True)
    with os.fdopen(fd, "w", encoding="oem", errors="replace") as f:
        f.write(f'select vdisk file="{distro["vhd"]}"\n'
                "attach vdisk readonly\ncompact vdisk\ndetach vdisk\n")
    try:
        _run(["diskpart.exe", "/s", script], timeout=3600)
    finally:
        os.remove(script)

    after = os.path.getsize(distro["vhd"])
    return (f"{distro['name']}: {format_size(before)} → {format_size(after)} "
            f"(giảm {format_size(max(before - after, 0))}).")


def wsl_move(distro: dict, dest_dir: str) -> str:
    target = os.path.join(dest_dir, distro["name"])
    _wsl("--terminate", distro["name"], timeout=120)
    _wsl("--manage", distro["name"], "--move", target, timeout=7200)
    return f"Đã chuyển {distro['name']} sang {target}."


# ---------------------------------------------------------------- WSL: giới hạn RAM

WSLCONFIG = os.path.join(os.environ.get("USERPROFILE", ""), ".wslconfig")
_UNITS = {"": 1, "B": 1, "K": 2**10, "KB": 2**10, "M": 2**20, "MB": 2**20,
          "G": 2**30, "GB": 2**30, "T": 2**40, "TB": 2**40}


def _read_wslconfig() -> list:
    try:
        with open(WSLCONFIG, encoding="utf-8-sig") as f:
            return f.read().splitlines()
    except OSError:
        return []


def _wsl2_memory_line(lines):
    """Chỉ số dòng `memory=` trong mục [wsl2] (hoặc None) và chỉ số dòng [wsl2] (hoặc None)."""
    section, header = None, None
    for i, line in enumerate(lines):
        text = line.strip()
        if text.startswith("["):
            section = text.strip("[]").strip().lower()
            if section == "wsl2":
                header = i
        elif section == "wsl2" and re.match(r"memory\s*=", text, re.I):
            return i, header
    return None, header


def wsl_memory_limit():
    """Giới hạn RAM của WSL (byte) theo .wslconfig, hoặc None nếu chưa đặt."""
    lines = _read_wslconfig()
    i, _ = _wsl2_memory_line(lines)
    if i is None:
        return None
    m = re.match(r"memory\s*=\s*([\d.]+)\s*([KMGT]?B?)\s*$", lines[i].strip(), re.I)
    if not m:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def set_wsl_memory_limit(gb) -> str:
    """gb=None: bỏ giới hạn (WSL mặc định dùng tối đa 50% RAM). Giữ nguyên các thiết lập khác."""
    lines = _read_wslconfig()
    i, header = _wsl2_memory_line(lines)
    if gb is None:
        if i is not None:
            del lines[i]
    elif i is not None:
        lines[i] = f"memory={gb}GB"
    elif header is not None:
        lines.insert(header + 1, f"memory={gb}GB")
    else:
        lines = (lines + [""] if lines and lines[-1].strip() else lines) + ["[wsl2]", f"memory={gb}GB"]
    try:
        with open(WSLCONFIG, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        raise RuntimeError(f"Không ghi được {WSLCONFIG}: {e}") from e
    what = "đã bỏ giới hạn (mặc định 50% RAM)" if gb is None else f"giới hạn {gb} GB"
    return f"RAM cho WSL (Ubuntu + Docker): {what}.\nCó hiệu lực sau khi tắt WSL."


def wsl_shutdown() -> str:
    _wsl("--shutdown", timeout=120)
    return ("Đã tắt WSL - giới hạn RAM mới đã có hiệu lực.\n"
            "Nếu đang dùng Docker Desktop, hãy mở lại Docker Desktop.")


# ---------------------------------------------------------------- WinSxS

_WINSXS_CACHE = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Phu_Don_Rac", "winsxs.json")
_WINSXS_FIELDS = {
    "Windows Explorer Reported Size of Component Store": "explorer",
    "Actual Size of Component Store": "actual",
    "Backups and Disabled Features": "backups",
    "Cache and Temporary Data": "cache",
    "Date of Last Cleanup": "last_cleanup",
    "Number of Reclaimable Packages": "reclaimable",
    "Component Store Cleanup Recommended": "recommended",
}


def _parse_size(text):
    m = re.match(r"([\d.,]+)\s*(bytes|[KMGT]B)", text.strip(), re.I)
    if not m:
        return None
    unit = m.group(2).upper()
    return int(float(m.group(1).replace(",", "")) * (1 if unit == "BYTES" else _UNITS[unit]))


def winsxs_info():
    """Kết quả phân tích WinSxS gần nhất (lưu từ lần chạy trước), hoặc None."""
    try:
        with open(_WINSXS_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def winsxs_analyze() -> str:
    """DISM /AnalyzeComponentStore (cần Admin, mất 1-5 phút); lưu kết quả để hiển thị."""
    out = _run(["dism.exe", "/English", "/Online", "/Cleanup-Image", "/AnalyzeComponentStore"],
               timeout=3600)
    info = {"checked": time.time()}
    for line in out.splitlines():
        key, sep, value = line.partition(":")
        field = _WINSXS_FIELDS.get(key.strip())
        if not sep or not field:
            continue
        value = value.strip()
        if field in ("explorer", "actual", "backups", "cache"):
            info[field] = _parse_size(value)
        elif field == "reclaimable":
            info[field] = int(value) if value.isdigit() else None
        elif field == "recommended":
            info[field] = value.lower() == "yes"
        else:
            info[field] = value
    if info.get("actual") is None:
        raise RuntimeError("Không đọc được kết quả của DISM:\n" + out[-1500:])
    os.makedirs(os.path.dirname(_WINSXS_CACHE), exist_ok=True)
    with open(_WINSXS_CACHE, "w", encoding="utf-8") as f:
        json.dump(info, f)
    return (f"Dung lượng thực của WinSxS: {format_size(info['actual'])}\n"
            f"Có thể dọn: {format_size((info.get('backups') or 0) + (info.get('cache') or 0))}"
            f"  ({info.get('reclaimable') or 0} gói cũ)\n"
            f"Lần dọn gần nhất: {format_date(info.get('last_cleanup'))}\n"
            f"Nên dọn: {'Có' if info.get('recommended') else 'Không'}")


def format_date(value) -> str:
    """'2026-09-15 11:01:05' -> '15/09/2026 11:01'."""
    try:
        return time.strftime("%d/%m/%Y %H:%M", time.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return value or "Chưa rõ"


def winsxs_cleanup() -> str:
    before = (winsxs_info() or {}).get("actual")
    _run(["dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], timeout=7200)
    msg = "Đã dọn WinSxS (Component Store)."
    try:
        winsxs_analyze()
    except RuntimeError:
        return msg
    after = winsxs_info()["actual"]
    if before:
        msg += f"\n{format_size(before)} → {format_size(after)} (giảm {format_size(max(before - after, 0))})."
    else:
        msg += f"\nDung lượng hiện tại: {format_size(after)}."
    return msg
