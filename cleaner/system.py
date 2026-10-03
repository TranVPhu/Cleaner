"""Tối ưu hệ thống: hiberfil.sys, pagefile.sys, ổ ảo WSL, WinSxS.

Các hàm `set_*` / `wsl_*` / `winsxs_cleanup` chạy lâu và phần lớn cần quyền Admin;
chúng ném RuntimeError kèm thông báo khi thất bại.
"""

import ctypes
import os
import subprocess
import tempfile
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


# ---------------------------------------------------------------- WinSxS

def winsxs_cleanup() -> str:
    _run(["dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], timeout=7200)
    return "Đã dọn WinSxS (Component Store)."
