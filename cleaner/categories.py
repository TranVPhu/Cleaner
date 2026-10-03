"""Định nghĩa các hạng mục rác có thể dọn.

Mỗi đường dẫn trong `paths` được mở rộng biến môi trường (%TEMP%, %LOCALAPPDATA%...)
và hỗ trợ ký tự đại diện glob (vd. "User Data\\*\\Cache" cho mọi profile trình duyệt).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    group: str
    paths: tuple = ()
    pattern: str = "*"          # chỉ xoá file khớp mẫu này
    recursive: bool = True      # quét cả thư mục con
    min_age_hours: float = 0    # chỉ xoá file cũ hơn N giờ
    admin: bool = False         # cần quyền Administrator
    default: bool = True        # được chọn sẵn
    kind: str = "files"         # "files" | "recycle_bin" | "old_versions"
    processes: tuple = ()       # tiến trình nên tắt trước khi dọn


def _chromium(id_, name, user_data, processes):
    """Cache của trình duyệt nhân Chromium (mọi profile)."""
    return Category(
        id=id_, name=name, group="Trình duyệt",
        paths=tuple(rf"{user_data}\*\{sub}" for sub in
                    ("Cache", "Code Cache", "GPUCache", "Service Worker\\CacheStorage")),
        processes=processes,
    )


CATEGORIES = [
    # ---------------- Windows ----------------
    Category("user_temp", "File tạm người dùng", "Windows",
             paths=(r"%TEMP%",), min_age_hours=24),
    Category("win_temp", "File tạm Windows", "Windows",
             paths=(r"%WINDIR%\Temp",), min_age_hours=24, admin=True),
    Category("recycle_bin", "Thùng rác", "Windows", kind="recycle_bin"),
    Category("thumbcache", "Bộ nhớ đệm hình thu nhỏ", "Windows",
             paths=(r"%LOCALAPPDATA%\Microsoft\Windows\Explorer",),
             pattern="thumbcache_*.db", recursive=False, default=False),
    Category("wer", "Báo cáo lỗi Windows", "Windows",
             paths=(r"%LOCALAPPDATA%\Microsoft\Windows\WER",
                    r"%PROGRAMDATA%\Microsoft\Windows\WER")),
    Category("crash_dumps", "Crash dump ứng dụng", "Windows",
             paths=(r"%LOCALAPPDATA%\CrashDumps",)),
    Category("d3d_cache", "Bộ nhớ đệm DirectX Shader", "Windows",
             paths=(r"%LOCALAPPDATA%\D3DSCache",)),
    Category("wu_cache", "Bộ đệm Windows Update", "Windows",
             paths=(r"%WINDIR%\SoftwareDistribution\Download",),
             admin=True, default=False),
    Category("delivery_opt", "Delivery Optimization", "Windows",
             paths=(r"%WINDIR%\ServiceProfiles\NetworkService\AppData\Local"
                    r"\Microsoft\Windows\DeliveryOptimization\Cache",),
             admin=True, default=False),

    # ---------------- Trình duyệt ----------------
    _chromium("chrome", "Google Chrome - cache",
              r"%LOCALAPPDATA%\Google\Chrome\User Data", ("chrome.exe",)),
    _chromium("edge", "Microsoft Edge - cache",
              r"%LOCALAPPDATA%\Microsoft\Edge\User Data", ("msedge.exe",)),
    _chromium("coccoc", "Cốc Cốc - cache",
              r"%LOCALAPPDATA%\CocCoc\Browser\User Data", ("browser.exe",)),
    _chromium("brave", "Brave - cache",
              r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data", ("brave.exe",)),
    Category("firefox", "Mozilla Firefox - cache", "Trình duyệt",
             paths=(r"%LOCALAPPDATA%\Mozilla\Firefox\Profiles\*\cache2",),
             processes=("firefox.exe",)),
    Category("opera", "Opera - cache", "Trình duyệt",
             paths=(r"%LOCALAPPDATA%\Opera Software\Opera Stable\Cache",
                    r"%LOCALAPPDATA%\Opera Software\Opera GX Stable\Cache"),
             processes=("opera.exe",)),

    # ---------------- Ứng dụng ----------------
    Category("nvidia", "NVIDIA shader cache", "Ứng dụng",
             paths=(r"%LOCALAPPDATA%\NVIDIA\DXCache", r"%LOCALAPPDATA%\NVIDIA\GLCache")),
    Category("discord", "Discord - cache", "Ứng dụng",
             paths=(r"%APPDATA%\discord\Cache", r"%APPDATA%\discord\Code Cache",
                    r"%APPDATA%\discord\GPUCache"),
             processes=("Discord.exe",)),
    Category("vscode", "VS Code - cache", "Ứng dụng",
             paths=(r"%APPDATA%\Code\Cache", r"%APPDATA%\Code\CachedData",
                    r"%APPDATA%\Code\GPUCache"),
             default=False, processes=("Code.exe",)),
    Category("vscode_ext", "VS Code - bộ cài extension, crash", "Ứng dụng",
             paths=(r"%APPDATA%\Code\CachedExtensionVSIXs", r"%APPDATA%\Code\Crashpad")),
    # Mỗi thư mục con là một phiên bản (vd. 9.4.0.4015); giữ lại bản mới nhất.
    Category("capcut_old", "CapCut - phiên bản cũ", "Ứng dụng",
             paths=(r"%LOCALAPPDATA%\CapCut\Apps",), kind="old_versions",
             processes=("CapCut.exe",)),
    Category("driver_booster", "Driver Booster - driver đã tải", "Ứng dụng",
             paths=(r"%PROGRAMDATA%\IObit\Driver Booster\Download",), admin=True,
             processes=("DriverBooster.exe",)),
    Category("driver_booster_bak", "Driver Booster - bản sao lưu driver", "Ứng dụng",
             paths=(r"%PROGRAMDATA%\IObit\Driver Booster\Backups",), admin=True,
             default=False, processes=("DriverBooster.exe",)),
    Category("pip", "pip cache (Python)", "Ứng dụng",
             paths=(r"%LOCALAPPDATA%\pip\cache",), default=False),
    Category("npm", "npm cache (Node.js)", "Ứng dụng",
             paths=(r"%LOCALAPPDATA%\npm-cache",), default=False),
]

BY_ID = {c.id: c for c in CATEGORIES}
