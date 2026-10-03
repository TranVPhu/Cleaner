"""Phu_Don_Rac - điểm khởi chạy.

    python main.py                       mở giao diện
    python main.py --list                liệt kê các hạng mục
    python main.py --scan [id ...]       chỉ quét (không xoá)
    python main.py --clean id ... --yes  quét và xoá
"""

import argparse
import sys

from cleaner import engine
from cleaner.categories import BY_ID, CATEGORIES


def _pick(ids):
    if not ids:
        return [c for c in CATEGORIES if c.default]
    unknown = [i for i in ids if i not in BY_ID]
    if unknown:
        sys.exit(f"Hạng mục không tồn tại: {', '.join(unknown)} (xem --list)")
    return [BY_ID[i] for i in ids]


def cli(args):
    if args.list:
        for c in CATEGORIES:
            flags = ("mặc định " if c.default else "") + ("admin" if c.admin else "")
            print(f"{c.id:14} {c.name:32} {flags}")
        return

    cats = _pick(args.clean if args.clean is not None else args.scan)
    if args.clean is not None and not args.yes:
        sys.exit("Thêm --yes để xác nhận xoá.")

    total = 0
    for cat in cats:
        res = engine.scan(cat)
        if args.clean is not None:
            out = engine.clean(res)
            total += out.freed
            print(f"{cat.name:32} đã xoá {out.deleted:>7,} file  "
                  f"{engine.format_size(out.freed):>10}  (bỏ qua {out.failed:,})")
        else:
            total += res.size
            print(f"{cat.name:32} {res.count:>7,} file  {engine.format_size(res.size):>10}")
    print(f"\nTổng: {engine.format_size(total)}")


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(description="Phu_Don_Rac - dọn dẹp file rác Windows")
    p.add_argument("--list", action="store_true", help="liệt kê hạng mục")
    p.add_argument("--scan", nargs="*", metavar="ID", help="chỉ quét")
    p.add_argument("--clean", nargs="*", metavar="ID", help="quét và xoá")
    p.add_argument("--yes", action="store_true", help="xác nhận xoá (dùng với --clean)")
    args = p.parse_args()

    if args.list or args.scan is not None or args.clean is not None:
        cli(args)
    else:
        from cleaner.gui import run
        run()


def _report_crash():
    """Ghi lỗi ra file và hiện hộp thoại (pythonw / .exe không có console để in lỗi)."""
    import os
    import traceback
    text = traceback.format_exc()
    log = os.path.join(os.environ.get("TEMP", "."), "Phu_Don_Rac-error.log")
    try:
        with open(log, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass
    if sys.stderr:
        print(text, file=sys.stderr)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Phu_Don_Rac - lỗi", text[-1500:] + f"\n\nLog: {log}")
        root.destroy()
    except Exception:
        pass
    sys.exit(1)


def _start_diagnostics():
    """Ghi nhật ký khởi động/thoát + lỗi native vào %TEMP%\\Phu_Don_Rac.log."""
    import atexit
    import faulthandler
    import os
    import time
    from cleaner import engine
    log = open(os.path.join(os.environ.get("TEMP", "."), "Phu_Don_Rac.log"), "a",
               encoding="utf-8", buffering=1)
    stamp = lambda: time.strftime("%Y-%m-%d %H:%M:%S")
    log.write(f"{stamp()} start pid={os.getpid()} admin={engine.is_admin()} "
              f"exe={sys.executable} argv={sys.argv} cwd={os.getcwd()}\n")
    faulthandler.enable(log)
    atexit.register(lambda: log.write(f"{stamp()} exit pid={os.getpid()}\n"))


if __name__ == "__main__":
    try:
        _start_diagnostics()
    except Exception:
        pass
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        _report_crash()
