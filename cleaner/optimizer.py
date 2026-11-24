"""Tối ưu Windows 11: tinh chỉnh registry, gỡ ứng dụng cài sẵn, tắt dịch vụ ít dùng.

Registry ghi trực tiếp bằng winreg; dịch vụ dùng sc.exe; ứng dụng UWP dùng PowerShell.
Các hàm `apply` / `revert` / `remove_apps` / `create_restore_point` trả về chuỗi tóm tắt
(kèm danh sách lỗi nếu có); `status()` đọc trạng thái hiện tại để giao diện hiển thị.
"""

import subprocess
import time
import winreg
from collections import namedtuple

from .engine import is_admin, running_processes
from .system import _run

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

HKCU, HKLM = winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE

# hive, đường dẫn, tên giá trị, kiểu, giá trị khi áp dụng, giá trị mặc định (None = xoá),
# optional: bị chặn thì bỏ qua (vd. UCPD bảo vệ một số khoá của Explorer)
Reg = namedtuple("Reg", "hive path name kind applied default optional", defaults=(False,))
# shell: cần khởi động lại Explorer mới thấy hiệu lực
Tweak = namedtuple("Tweak", "id label default regs services shell", defaults=((), False))
# start: kiểu khởi động mặc định của Windows (auto | delayed-auto | demand)
Service = namedtuple("Service", "name label default start")

_BG = r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications"
_APPPRIV = r"SOFTWARE\Policies\Microsoft\Windows\AppPrivacy"
_ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
_PERS = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_CDM = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
_DWORD, _SZ = winreg.REG_DWORD, winreg.REG_SZ

TWEAKS = [
    Tweak("bg_apps", "Tắt ứng dụng Store chạy nền - tiết kiệm RAM (có thể mất thông báo "
                     "của Teams, Phone Link, báo thức...)", True, (
        Reg(HKCU, _BG, "GlobalUserDisabled", _DWORD, 1, 0),
        Reg(HKLM, _APPPRIV, "LetAppsRunInBackground", _DWORD, 2, None))),
    Tweak("widgets", "Tắt Widgets & Bảng tin (giảm RAM từ Edge WebView2)", True, (
        Reg(HKLM, r"SOFTWARE\Policies\Microsoft\Dsh", "AllowNewsAndInterests", _DWORD, 0, None),
        Reg(HKCU, _ADV, "TaskbarDa", _DWORD, 0, 1, True)), shell=True),
    Tweak("telemetry", "Giảm telemetry xuống mức tối thiểu & tắt dịch vụ DiagTrack "
                       "(bản Home không tắt hẳn được)", True, (
        Reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection",
            "AllowTelemetry", _DWORD, 0, None),),
        services=(("DiagTrack", "auto"), ("dmwappushservice", "demand"))),
    Tweak("transparency", "Tắt hiệu ứng trong suốt & hoạt ảnh thừa", True, (
        Reg(HKCU, _PERS, "EnableTransparency", _DWORD, 0, 1),
        Reg(HKCU, r"Control Panel\Desktop\WindowMetrics", "MinAnimate", _SZ, "0", "1")),
        shell=True),
    Tweak("bing", "Tắt tìm kiếm Bing/Web trong Start Menu", True, (
        Reg(HKCU, r"Software\Policies\Microsoft\Windows\Explorer",
            "DisableSearchBoxSuggestions", _DWORD, 1, None),), shell=True),
    Tweak("ads", "Tắt gợi ý, mẹo và quảng cáo ứng dụng", True, tuple(
        Reg(HKCU, _CDM, n, _DWORD, 0, 1) for n in (
            "SubscribedContent-338388Enabled", "SubscribedContent-338389Enabled",
            "SubscribedContent-353694Enabled", "SubscribedContent-353696Enabled",
            "SystemPaneSuggestionsEnabled", "SilentInstalledAppsEnabled",
            "SoftLandingEnabled"))),
    Tweak("gamebar", "Tắt Xbox Game Bar & Game DVR chạy ngầm", True, (
        Reg(HKCU, r"System\GameConfigStore", "GameDVR_Enabled", _DWORD, 0, 1),
        Reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR", _DWORD, 0, None))),
]

# (tên gói, mô tả, chọn mặc định)
APPS = [
    ("Clipchamp.Clipchamp", "Microsoft Clipchamp (sửa video)", True),
    ("Microsoft.BingNews", "Microsoft News (Tin tức)", True),
    ("Microsoft.BingWeather", "Microsoft Weather (Thời tiết)", True),
    ("Microsoft.BingSearch", "Bing Search (tìm kiếm web trong Windows Search)", True),
    ("Microsoft.549981C3F5F10", "Cortana", True),
    ("Microsoft.MicrosoftSolitaireCollection", "Solitaire & Casual Games", True),
    ("Microsoft.WindowsFeedbackHub", "Feedback Hub", True),
    ("Microsoft.GetHelp", "Get Help", True),
    ("Microsoft.Getstarted", "Get Started / Tips", True),
    ("Microsoft.People", "Microsoft People", True),
    ("Microsoft.WindowsMaps", "Windows Maps (Bản đồ)", True),
    ("Microsoft.Microsoft3DViewer", "3D Viewer", True),
    ("Microsoft.PowerAutomateDesktop", "Power Automate Desktop", True),
    ("Microsoft.Windows.DevHome", "Dev Home", True),
    ("Microsoft.Todos", "Microsoft To Do", False),
    ("Microsoft.OutlookForWindows", "Outlook (mới)", False),
    ("MSTeams", "Microsoft Teams (bản mới cài sẵn)", False),
    ("MicrosoftTeams", "Microsoft Teams (bản cá nhân cũ)", False),
    ("Microsoft.Copilot", "Microsoft Copilot", False),
    ("Microsoft.YourPhone", "Phone Link (kết nối điện thoại)", False),
    ("Microsoft.GamingApp", "Xbox App", False),
    ("Microsoft.XboxGamingOverlay", "Xbox Game Bar Overlay", False),
    ("MicrosoftCorporationII.QuickAssist", "Quick Assist", False),
]

SERVICES = [
    Service("SysMain", "SysMain / Superfetch (giảm RAM/CPU trên SSD)", True, "auto"),
    Service("MapsBroker", "Downloaded Maps Manager (bản đồ offline)", True, "delayed-auto"),
    Service("Fax", "Fax Service (dịch vụ Fax cũ)", True, "demand"),
    Service("WerSvc", "Windows Error Reporting (gửi báo cáo lỗi)", True, "demand"),
    Service("XblAuthManager", "Xbox Live Auth Manager", False, "demand"),
    Service("XblGameSave", "Xbox Live Game Save", False, "demand"),
    Service("XboxNetApiSvc", "Xbox Live Networking Service", False, "demand"),
]

_TWEAKS = {t.id: t for t in TWEAKS}
_SERVICES = {s.name: s for s in SERVICES}


# ---------------------------------------------------------------- registry

def _read(reg):
    try:
        with winreg.OpenKey(reg.hive, reg.path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return winreg.QueryValueEx(k, reg.name)[0]
    except OSError:
        return None


def _write(reg, value):
    """value=None: xoá giá trị. Ném OSError khi bị chặn."""
    if value is None:
        try:
            with winreg.OpenKey(reg.hive, reg.path, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
                winreg.DeleteValue(k, reg.name)
        except FileNotFoundError:
            pass
        return
    with winreg.CreateKeyEx(reg.hive, reg.path, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, reg.name, 0, reg.kind, value)


# ---------------------------------------------------------------- dịch vụ

def _service_start(name):
    """Kiểu khởi động hiện tại: 'disabled' | 'auto' | 'delayed-auto' | 'demand', None nếu không có."""
    try:
        with winreg.OpenKey(HKLM, rf"SYSTEM\CurrentControlSet\Services\{name}") as k:
            start = winreg.QueryValueEx(k, "Start")[0]
            try:
                delayed = winreg.QueryValueEx(k, "DelayedAutostart")[0] == 1
            except OSError:
                delayed = False
    except OSError:
        return None
    if start == 2:
        return "delayed-auto" if delayed else "auto"
    return {4: "disabled", 3: "demand", 0: "auto", 1: "auto"}.get(start, "demand")


def _service_set(name, start):
    _run(["sc.exe", "config", name, "start=", start])
    try:
        if start == "disabled":
            _run(["sc.exe", "stop", name])
        elif start != "demand":
            _run(["sc.exe", "start", name])
    except RuntimeError:
        pass                    # đã dừng / đã chạy / không dừng được ngay - không quan trọng


# ---------------------------------------------------------------- ứng dụng UWP

def _powershell(script, timeout=None):
    try:
        p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-Command", script],
                           capture_output=True, creationflags=_NO_WINDOW, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"Không chạy được PowerShell: {e}") from e
    out = (p.stdout + p.stderr).decode("oem", errors="replace").strip()
    if p.returncode != 0:
        raise RuntimeError(out or f"PowerShell lỗi (mã {p.returncode})")
    return out


def installed_apps():
    """Tên các gói UWP đang cài (chữ thường)."""
    flag = " -AllUsers" if is_admin() else ""
    out = _powershell(f"Get-AppxPackage{flag} | Select-Object -ExpandProperty Name", timeout=120)
    return {line.strip().lower() for line in out.splitlines() if line.strip()}


def _is_installed(pkg, installed):
    pkg = pkg.lower()
    return any(pkg in name for name in installed)


# ---------------------------------------------------------------- trạng thái

def status():
    """{'tweaks': {id: đã áp dụng?}, 'services': {tên: kiểu khởi động | None},
    'apps': {gói: đang cài?} hoặc None nếu đọc lỗi}."""
    tweaks = {}
    for t in TWEAKS:
        ok = all(_read(r) == r.applied for r in t.regs if not r.optional)
        ok = ok and all(_service_start(n) in (None, "disabled") for n, _ in t.services)
        tweaks[t.id] = ok
    services = {s.name: _service_start(s.name) for s in SERVICES}
    try:
        installed = installed_apps()
        apps = {pkg: _is_installed(pkg, installed) for pkg, _, _ in APPS}
    except RuntimeError:
        apps = None
    return {"tweaks": tweaks, "services": services, "apps": apps}


# ---------------------------------------------------------------- hành động

def _summary(text, errors, notes=()):
    if notes:
        text += "\n\n" + "\n".join(notes)
    if errors:
        text += "\n\nLỗi:\n  " + "\n  ".join(errors)
    return text


def _set_tweak(tweak, apply):
    """Áp dụng / khôi phục một tinh chỉnh. Trả về (lỗi hoặc None, ghi chú hoặc None)."""
    note = None
    try:
        for r in tweak.regs:
            try:
                _write(r, r.applied if apply else r.default)
            except OSError as e:
                if not r.optional:
                    why = ("bị từ chối - cần quyền Admin" if isinstance(e, PermissionError)
                           else (e.strerror or str(e)))
                    raise RuntimeError(f"{r.name}: {why}") from e
                note = f"Windows bảo vệ khoá '{r.name}' nên bỏ qua (không ảnh hưởng kết quả chính)."
        for name, default in tweak.services:
            if _service_start(name) is not None:
                _service_set(name, "disabled" if apply else default)
    except RuntimeError as e:
        return f"{tweak.label.split(' (')[0]}: {str(e).splitlines()[0]}", note
    return None, note


class _Steps:
    """Báo tiến độ: gọi `step(mô_tả)` ngay trước mỗi bước; hoàn tất khi hết `total` bước."""

    def __init__(self, total, progress):
        self.total, self.progress, self.done = total, progress, 0

    def __call__(self, text):
        if self.progress:
            self.progress(self.done, self.total, text)
        self.done += 1


def create_restore_point() -> str:
    """Tạo điểm khôi phục (tạm bỏ giới hạn 1 điểm/24 giờ) và kiểm tra là thật sự có điểm mới."""
    _powershell(r"""
$k = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore'
$n = 'SystemRestorePointCreationFrequency'
$old = (Get-ItemProperty $k -Name $n -ErrorAction SilentlyContinue).$n
Set-ItemProperty $k -Name $n -Value 0 -Type DWord
try {
    Enable-ComputerRestore -Drive "$env:SystemDrive\" -ErrorAction SilentlyContinue
    $before = (Get-ComputerRestorePoint -ErrorAction SilentlyContinue | Measure-Object SequenceNumber -Maximum).Maximum
    Checkpoint-Computer -Description 'Phu_Don_Rac_Backup' -RestorePointType MODIFY_SETTINGS -ErrorAction Stop -WarningAction SilentlyContinue
    $after = (Get-ComputerRestorePoint -ErrorAction SilentlyContinue | Measure-Object SequenceNumber -Maximum).Maximum
    if (-not $after -or $after -eq $before) { throw 'Windows không tạo điểm khôi phục (System Protection có thể đang tắt).' }
} finally {
    if ($null -eq $old) { Remove-ItemProperty $k -Name $n -ErrorAction SilentlyContinue }
    else { Set-ItemProperty $k -Name $n -Value $old -Type DWord }
}
""", timeout=600)
    return "Đã tạo điểm khôi phục 'Phu_Don_Rac_Backup'."


def apply(tweak_ids, service_names, progress=None) -> str:
    errors, notes, t, s = [], [], 0, 0
    step = _Steps(len(tweak_ids) + len(service_names), progress)
    for tid in tweak_ids:
        step(_TWEAKS[tid].label.split(" (")[0].split(" - ")[0])
        err, note = _set_tweak(_TWEAKS[tid], True)
        if err:
            errors.append(err)
        else:
            t += 1
        if note and note not in notes:
            notes.append(note)
    for name in service_names:
        step(name)
        try:
            _service_set(name, "disabled")
            s += 1
        except RuntimeError as e:
            errors.append(f"{name}: {str(e).splitlines()[0]}")
    if any(_TWEAKS[i].shell for i in tweak_ids):
        notes.append("Một số tinh chỉnh cần khởi động lại Explorer (nút ở góc trên) hoặc đăng xuất.")
    return _summary(f"Đã áp dụng {t} tinh chỉnh, tắt {s} dịch vụ.", errors, notes)


def revert(tweak_ids, service_names, progress=None) -> str:
    errors, notes, t, s = [], [], 0, 0
    step = _Steps(len(tweak_ids) + len(service_names), progress)
    for tid in tweak_ids:
        step(_TWEAKS[tid].label.split(" (")[0].split(" - ")[0])
        err, note = _set_tweak(_TWEAKS[tid], False)
        if err:
            errors.append(err)
        else:
            t += 1
        if note and note not in notes:
            notes.append(note)
    for name in service_names:
        step(name)
        try:
            _service_set(name, _SERVICES[name].start)
            s += 1
        except RuntimeError as e:
            errors.append(f"{name}: {str(e).splitlines()[0]}")
    if any(_TWEAKS[i].shell for i in tweak_ids):
        notes.append("Một số tinh chỉnh cần khởi động lại Explorer (nút ở góc trên) hoặc đăng xuất.")
    return _summary(f"Đã khôi phục {t} tinh chỉnh, {s} dịch vụ về mặc định Windows.", errors, notes)


def remove_apps(packages, progress=None) -> str:
    if progress:
        progress(0, 1, "Đang kiểm tra ứng dụng đã cài")
    before = installed_apps()
    todo = [p for p in packages if _is_installed(p, before)]
    skipped = len(packages) - len(todo)
    step = _Steps(len(todo), progress)
    for p in todo:
        step(p)
        try:
            _powershell(
                f'Get-AppxPackage -AllUsers "*{p}*" | Remove-AppxPackage -AllUsers '
                f'-ErrorAction SilentlyContinue; '
                f'Get-AppxProvisionedPackage -Online | Where-Object {{$_.DisplayName -like "*{p}*"}} | '
                f'Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue | Out-Null',
                timeout=600)
        except RuntimeError:
            pass                # kiểm tra lại ở dưới
    after = installed_apps() if todo else before
    failed = [p for p in todo if _is_installed(p, after)]
    text = f"Đã gỡ {len(todo) - len(failed)}/{len(todo)} ứng dụng."
    if skipped:
        text += f"\nBỏ qua {skipped} ứng dụng không có trên máy."
    errors = [f"{p}: vẫn còn (bị Windows bảo vệ hoặc cần quyền cao hơn)" for p in failed]
    return _summary(text + "\nCó thể cài lại từ Microsoft Store.", errors)


def restart_explorer() -> str:
    try:
        _run(["taskkill.exe", "/f", "/im", "explorer.exe"])
    except RuntimeError:
        pass
    time.sleep(2)
    if "explorer.exe" not in running_processes():
        subprocess.Popen(["explorer.exe"], creationflags=_NO_WINDOW)
    return "Đã khởi động lại Explorer."
