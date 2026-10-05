"""Giao diện Tkinter."""

import ctypes
import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, engine, memory, system
from .categories import CATEGORIES

ACCENT = "#1f6feb"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Phu_Don_Rac {__version__} - Dọn dẹp ổ đĩa")
        self.geometry("1040x700")
        self.minsize(860, 560)
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            self.iconbitmap(os.path.join(base, "icon.ico"))
        except tk.TclError:
            pass

        self.admin = engine.is_admin()
        self.vars = {}            # id -> BooleanVar
        self.results = {}         # id -> ScanResult (lần quét gần nhất)
        self.queue = queue.Queue()
        self.busy = False

        self._style()
        self._build()
        self.after(100, self._poll)

    def report_callback_exception(self, exc, val, tb):
        """Lỗi trong callback Tk: ghi log và báo, thay vì in ra console vô hình."""
        import traceback
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            with open(ERROR_LOG, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        messagebox.showerror("Phu_Don_Rac - lỗi", text[-1500:])

    # ------------------------------------------------------------ giao diện
    def _style(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 15))
        style.configure("Big.TLabel", font=("Segoe UI Semibold", 22), foreground=ACCENT)
        style.configure("Muted.TLabel", foreground="#666")
        style.configure("Value.TLabel", font=("Segoe UI Semibold", 11))
        style.configure("Treeview", rowheight=26)
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=(16, 6))

    def _build(self):
        # Thanh trên cùng
        top = ttk.Frame(self, padding=(14, 10))
        top.pack(fill="x")
        ttk.Label(top, text="Phu_Don_Rac", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="  Dọn file tạm, cache và thùng rác",
                  style="Muted.TLabel").pack(side="left")
        ttk.Button(top, text="ⓘ Thông tin", command=self._about).pack(side="right", padx=(8, 0))
        if self.admin:
            ttk.Label(top, text="✔ Đang chạy với quyền Administrator",
                      foreground="#1a7f37").pack(side="right")
        else:
            ttk.Button(top, text="Chạy với quyền Admin",
                       command=self._relaunch_as_admin).pack(side="right")
            ttk.Label(top, text="Không có quyền Admin - một số mục bị bỏ qua  ",
                      style="Muted.TLabel").pack(side="right")

        ttk.Separator(self).pack(fill="x")

        # Thanh trạng thái (pack trước để luôn có chỗ ở đáy cửa sổ)
        bottom = ttk.Frame(self, padding=(10, 4, 10, 8))
        bottom.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x")
        self.status = ttk.Label(bottom, text="Sẵn sàng.", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(4, 0))

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        body = ttk.Frame(notebook, padding=10)
        notebook.add(body, text="  Dọn dẹp  ")
        system_tab = ttk.Frame(notebook, padding=10)
        notebook.add(system_tab, text="  Tối ưu hệ thống  ")
        self.system_panel = SystemPanel(system_tab, self)
        self.system_panel.pack(fill="both", expand=True)
        memory_tab = ttk.Frame(notebook, padding=10)
        notebook.add(memory_tab, text="  RAM & Khởi động  ")
        self.memory_panel = MemoryPanel(memory_tab, self)
        self.memory_panel.pack(fill="both", expand=True)
        notebook.bind("<<NotebookTabChanged>>", lambda e: (
            self.system_panel.refresh() if notebook.index("current") == 1 else
            self.memory_panel.refresh() if notebook.index("current") == 2 else None))

        # Cột trái: danh sách hạng mục (cuộn được)
        left_outer = ttk.Frame(body)
        left_outer.pack(side="left", fill="y", padx=(0, 10))
        scroll = _Scrollable(left_outer)
        scroll.pack(fill="both", expand=True)
        left = scroll.inner
        groups = {}
        for cat in CATEGORIES:
            if cat.group not in groups:
                groups[cat.group] = ttk.LabelFrame(left, text=cat.group, padding=(8, 4))
                groups[cat.group].pack(fill="x", pady=(0, 6), padx=(0, 4))
            var = tk.BooleanVar(value=cat.default and (self.admin or not cat.admin))
            label = cat.name + ("  (Admin)" if cat.admin and not self.admin else "")
            cb = ttk.Checkbutton(groups[cat.group], text=label, variable=var)
            cb.pack(anchor="w")
            self.vars[cat.id] = var

        sel = ttk.Frame(left_outer)
        sel.pack(fill="x", pady=(6, 0))
        ttk.Button(sel, text="Chọn tất cả", command=lambda: self._select(True)).pack(side="left")
        ttk.Button(sel, text="Bỏ chọn", command=lambda: self._select(False)).pack(side="left", padx=4)
        ttk.Button(sel, text="Mặc định", command=self._select_default).pack(side="left")

        # Cột phải: kết quả
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        summary = ttk.Frame(right)
        summary.pack(fill="x", pady=(0, 8))
        self.total_label = ttk.Label(summary, text="—", style="Big.TLabel")
        self.total_label.pack(side="left")
        self.summary_label = ttk.Label(summary, text="Bấm \"Phân tích\" để quét rác.",
                                       style="Muted.TLabel")
        self.summary_label.pack(side="left", padx=12, pady=(8, 0))

        cols = ("name", "files", "size", "status")
        tree_frame = ttk.Frame(right)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for col, text, width, anchor in (
            ("name", "Hạng mục", 260, "w"), ("files", "Số file", 90, "e"),
            ("size", "Dung lượng", 110, "e"), ("status", "Trạng thái", 220, "w"),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor, stretch=(col in ("name", "status")))
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._open_folder)

        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(10, 0))
        self.clean_btn = ttk.Button(buttons, text="Dọn dẹp", style="Accent.TButton",
                                    command=self._start_clean)
        self.clean_btn.pack(side="right")
        self.scan_btn = ttk.Button(buttons, text="Phân tích", style="Accent.TButton",
                                   command=self._start_scan)
        self.scan_btn.pack(side="right", padx=8)
        ttk.Label(buttons, text="Nhấp đúp một dòng để mở thư mục",
                  style="Muted.TLabel").pack(side="left")

    def _about(self):
        messagebox.showinfo("Thông tin", f"Phu_Don_Rac {__version__}\n\n"
                                         "Author: Tran Van Phu\n"
                                         "Release date: 05/10/2026")

    def _select(self, value):
        for var in self.vars.values():
            var.set(value)

    def _select_default(self):
        for cat in CATEGORIES:
            self.vars[cat.id].set(cat.default and (self.admin or not cat.admin))

    def _selected(self):
        return [c for c in CATEGORIES if self.vars[c.id].get()]

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.scan_btn.configure(state=state)
        self.clean_btn.configure(state=state)
        self.system_panel.set_busy(busy)
        self.memory_panel.set_busy(busy)

    def run_task(self, label, func, *args):
        """Chạy `func(*args)` ở luồng phụ; kết quả (chuỗi) hiện bằng hộp thoại."""
        if self.busy:
            return
        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        self.status.configure(text=label + "…")
        drive = system.SYSTEM_DRIVE + "\\"
        free_before = shutil.disk_usage(drive).free

        def work():
            try:
                ok, msg = True, func(*args)
            except Exception as e:      # báo mọi lỗi lên giao diện
                ok, msg = False, str(e)
            freed = shutil.disk_usage(drive).free - free_before
            self.queue.put(("task_done", ok, label, msg, freed))

        threading.Thread(target=work, daemon=True).start()

    def _on_task_done(self, ok, label, msg, freed):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._set_busy(False)
        self.system_panel.refresh()
        self.memory_panel.refresh()
        if ok:
            if freed > 50 * 2**20:
                msg += f"\n\nỔ {system.SYSTEM_DRIVE} vừa trống thêm {engine.format_size(freed)}."
            self.status.configure(text=f"{label}: xong.")
            messagebox.showinfo(label, msg)
        else:
            self.status.configure(text=f"{label}: lỗi.")
            messagebox.showerror(label, msg[-2000:])

    def _open_folder(self, _event):
        item = self.tree.focus()
        res = self.results.get(item)
        if res and res.roots:
            os.startfile(res.roots[0])

    def _upsert_row(self, cat_id, name, files, size, status):
        values = (name, files, size, status)
        if self.tree.exists(cat_id):
            self.tree.item(cat_id, values=values)
        else:
            self.tree.insert("", "end", iid=cat_id, values=values)

    # ------------------------------------------------------------ hành động
    def _start_scan(self):
        cats = self._selected()
        if not cats:
            messagebox.showinfo("Phu_Don_Rac", "Hãy chọn ít nhất một hạng mục.")
            return
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self.total_label.configure(text="…")
        self._set_busy(True)
        threading.Thread(target=self._scan_worker, args=(cats,), daemon=True).start()

    def _scan_worker(self, cats):
        for i, cat in enumerate(cats):
            self.queue.put(("progress", i / len(cats), f"Đang phân tích: {cat.name}"))
            self.queue.put(("scanned", engine.scan(cat)))
        self.queue.put(("scan_done", None))

    def _start_clean(self):
        cats = self._selected()
        if not cats:
            messagebox.showinfo("Phu_Don_Rac", "Hãy chọn ít nhất một hạng mục.")
            return

        running = engine.running_processes()
        open_apps = sorted({p for c in cats for p in c.processes if p.lower() in running})
        msg = "Xoá vĩnh viễn các file rác trong những hạng mục đã chọn?"
        known = [self.results[c.id] for c in cats if c.id in self.results]
        if known:
            msg += f"\n\nƯớc tính giải phóng: {engine.format_size(sum(r.size for r in known))}"
        if open_apps:
            msg += ("\n\nCác ứng dụng sau đang chạy, nên đóng lại để dọn được nhiều hơn:\n  "
                    + ", ".join(open_apps))
        if not messagebox.askyesno("Xác nhận dọn dẹp", msg, icon="warning"):
            return

        self._set_busy(True)
        threading.Thread(target=self._clean_worker, args=(cats,), daemon=True).start()

    def _clean_worker(self, cats):
        total = engine.CleanResult()
        n = len(cats)
        for i, cat in enumerate(cats):
            self.queue.put(("progress", i / n, f"Đang dọn: {cat.name}"))
            res = engine.scan(cat)      # quét lại để có danh sách mới nhất
            out = engine.clean(
                res, lambda p, i=i: self.queue.put(("progress", (i + p) / n, None)))
            total.freed += out.freed
            total.deleted += out.deleted
            total.failed += out.failed
            self.queue.put(("cleaned", (res, out)))
        self.queue.put(("clean_done", total))

    # ------------------------------------------------------------ nhận kết quả từ luồng phụ
    def _poll(self):
        try:
            while True:
                kind, *data = self.queue.get_nowait()
                getattr(self, "_on_" + kind)(*data)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_progress(self, value, text):
        self.progress["value"] = value
        if text:
            self.status.configure(text=text)

    def _on_scanned(self, res):
        self.results[res.category.id] = res
        cat = res.category
        if cat.admin and not self.admin:
            status = "Cần quyền Admin" if not res.count else "Một phần (cần Admin)"
        elif cat.kind == "files" and not res.roots:
            status = "Không tìm thấy"
        else:
            status = "Đã phân tích"
        self._upsert_row(cat.id, cat.name, f"{res.count:,}", engine.format_size(res.size), status)
        total = sum(r.size for r in self.results.values())
        self.total_label.configure(text=engine.format_size(total))

    def _on_scan_done(self, _):
        total = sum(r.size for r in self.results.values())
        count = sum(r.count for r in self.results.values())
        self.progress["value"] = 1.0
        self.total_label.configure(text=engine.format_size(total))
        self.summary_label.configure(text=f"có thể giải phóng ({count:,} file)")
        self.status.configure(text="Phân tích hoàn tất.")
        self._set_busy(False)

    def _on_cleaned(self, payload):
        res, out = payload
        cat = res.category
        status = "Đã dọn" + (f" - bỏ qua {out.failed:,} file đang dùng" if out.failed else "")
        self._upsert_row(cat.id, cat.name, f"{out.deleted:,}", engine.format_size(out.freed), status)
        self.results.pop(cat.id, None)

    def _on_clean_done(self, total):
        self.progress["value"] = 1.0
        self.total_label.configure(text=engine.format_size(total.freed))
        self.summary_label.configure(text=f"đã giải phóng ({total.deleted:,} file)")
        self.status.configure(text="Dọn dẹp hoàn tất."
                              + (f" {total.failed:,} file đang được sử dụng nên bị bỏ qua."
                                 if total.failed else ""))
        self._set_busy(False)

    # ------------------------------------------------------------ quyền admin
    def _relaunch_as_admin(self):
        if getattr(sys, "frozen", False):        # bản .exe (PyInstaller)
            exe, params, workdir = sys.executable, "", os.path.dirname(sys.executable)
        else:
            exe = sys.executable
            pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
            if os.path.exists(pythonw):
                exe = pythonw
            script = os.path.abspath(sys.argv[0])
            params, workdir = f'"{script}"', os.path.dirname(script)

        try:
            os.remove(ERROR_LOG)
        except OSError:
            pass
        ok, err, proc = _shell_execute_runas(exe, params, workdir)
        if not ok:
            if err != 1223:                      # 1223 = người dùng bấm "No" ở UAC
                messagebox.showerror("Phu_Don_Rac", f"Không thể chạy với quyền Admin (mã lỗi {err}).")
            return

        # Chờ một chút: nếu bản Admin thoát ngay thì nó đã lỗi -> giữ cửa sổ này lại.
        self.status.configure(text="Đang khởi động lại với quyền Admin…")
        self.update()
        kernel32 = ctypes.windll.kernel32
        exited = kernel32.WaitForSingleObject(proc, 3000) == 0
        if not exited:
            # Không thoát ngay: nếu tiến trình này nằm trong job "kill on close"
            # (vd. chạy bằng debugger của VS Code) thì bản Admin sẽ bị tắt theo.
            # Vì vậy chỉ ẩn cửa sổ và chờ bản Admin đóng rồi mới thoát.
            self.withdraw()
            self._wait_for_child(proc)
            return
        kernel32.CloseHandle(proc)
        detail = ""
        try:
            with open(ERROR_LOG, encoding="utf-8") as f:
                detail = "\n\n" + f.read()[-1500:]
        except OSError:
            pass
        self.status.configure(text="Sẵn sàng.")
        messagebox.showerror("Phu_Don_Rac", "Bản chạy với quyền Admin bị lỗi khi khởi động."
                             + detail + f"\n\nLog: {ERROR_LOG}")

    def _wait_for_child(self, proc):
        kernel32 = ctypes.windll.kernel32
        if kernel32.WaitForSingleObject(proc, 0) == 0:
            kernel32.CloseHandle(proc)
            self.destroy()
        else:
            self.after(500, self._wait_for_child, proc)


ERROR_LOG = os.path.join(os.environ.get("TEMP", "."), "Phu_Don_Rac-error.log")


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_ulong),
                ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
                ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
                ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                ("dwHotKey", ctypes.c_uint32), ("hIcon", ctypes.c_void_p),
                ("hProcess", ctypes.c_void_p)]


def _shell_execute_runas(exe, params, workdir):
    """Chạy `exe` với quyền Admin. Trả về (thành công, mã lỗi, handle tiến trình)."""
    SEE_MASK_NOCLOSEPROCESS = 0x40
    info = _SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(_SHELLEXECUTEINFOW),
                              fMask=SEE_MASK_NOCLOSEPROCESS, lpVerb="runas",
                              lpFile=exe, lpParameters=params, lpDirectory=workdir, nShow=1)
    fn = ctypes.windll.shell32.ShellExecuteExW
    fn.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    if not fn(ctypes.byref(info)):
        return False, ctypes.GetLastError(), None
    return True, 0, ctypes.c_void_p(info.hProcess)


class _Scrollable(ttk.Frame):
    """Khung cuộn dọc; đặt widget con vào `self.inner`."""

    def __init__(self, parent):
        super().__init__(parent)
        bg = ttk.Style(self).lookup("TFrame", "background") or "SystemButtonFace"
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=bg)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def _on_inner_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"),
                              width=self.inner.winfo_reqwidth())

    def _on_wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        if (widget is not None and str(widget).startswith(str(self))
                and self.inner.winfo_height() > self.canvas.winfo_height()):
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class SystemPanel(ttk.Frame):
    """Tab "Tối ưu hệ thống": hiberfil, pagefile, WSL, WinSxS."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.buttons = []          # [(button, cần_admin)]
        self.distros = []

        head = ttk.Frame(self)
        head.pack(fill="x", pady=(0, 8))
        if not app.admin:
            ttk.Label(head, text="Phần lớn thao tác ở đây cần quyền Admin - "
                                 "bấm \"Chạy với quyền Admin\" ở góc trên.",
                      foreground="#b35900").pack(side="left")
        ttk.Button(head, text="Làm mới", command=self.refresh).pack(side="right")

        cols = ttk.Frame(self)
        cols.pack(fill="both", expand=True)
        left = ttk.Frame(cols)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        right = ttk.Frame(cols)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))

        # --- Hibernation
        card = self._card(left, "Ngủ đông - hiberfil.sys")
        self.hib_label = ttk.Label(card, style="Value.TLabel")
        self.hib_label.pack(anchor="w")
        self._desc(card, "Rút gọn: giữ Fast Startup, bỏ chế độ Hibernate - file nhỏ khoảng một nửa.\n"
                         "Tắt hẳn: xoá hiberfil.sys, mất cả Hibernate và Fast Startup.")
        row = ttk.Frame(card)
        row.pack(anchor="w")
        self._button(row, "Rút gọn", lambda: self._hibernate("reduced"), admin=True)
        self._button(row, "Tắt hẳn", lambda: self._hibernate("off"), admin=True)
        self._button(row, "Bật đầy đủ", lambda: self._hibernate("full"), admin=True)

        # --- Pagefile
        card = self._card(left, "Bộ nhớ ảo - pagefile.sys")
        self.pf_label = ttk.Label(card, style="Value.TLabel")
        self.pf_label.pack(anchor="w")
        self._desc(card, "Khi để Windows tự quản lý, file này có thể phình rất to. "
                         "Với máy 16 GB RAM, đặt cố định 4-8 GB là đủ cho đa số nhu cầu. "
                         "Cần khởi động lại máy để có hiệu lực.")
        row = ttk.Frame(card)
        row.pack(anchor="w")
        self.pf_gb = tk.StringVar(value="8")
        ttk.Spinbox(row, from_=2, to=64, width=4, textvariable=self.pf_gb).pack(side="left")
        ttk.Label(row, text=" GB  ").pack(side="left")
        self._button(row, "Đặt cố định", lambda: self._pagefile(auto=False), admin=True)
        self._button(row, "Windows tự quản lý", lambda: self._pagefile(auto=True), admin=True)

        # --- WSL
        card = self._card(right, "Ổ ảo WSL - ext4.vhdx")
        tree_frame = ttk.Frame(card)
        tree_frame.pack(fill="x")
        self.wsl_tree = ttk.Treeview(tree_frame, columns=("name", "size", "path"),
                                     show="headings", height=3, selectmode="browse")
        for col, text, width, anchor in (("name", "Distro", 170, "w"),
                                         ("size", "Dung lượng", 90, "e"),
                                         ("path", "Vị trí", 260, "w")):
            self.wsl_tree.heading(col, text=text)
            self.wsl_tree.column(col, width=width, anchor=anchor, stretch=(col == "path"))
        self.wsl_tree.tag_configure("docker", foreground="#888")
        self.wsl_tree.pack(fill="both", expand=True)
        self._desc(card, "File .vhdx không tự co lại khi xoá file trong Linux. "
                         "Thu gọn: dọn apt cache, TRIM rồi nén file (WSL sẽ bị tắt). "
                         "Chuyển: dời cả distro sang ổ khác, giải phóng toàn bộ trên C:.")
        row = ttk.Frame(card)
        row.pack(anchor="w")
        self._button(row, "Dọn & thu gọn", self._wsl_compact, admin=True)
        self._button(row, "Chuyển sang ổ khác…", self._wsl_move, admin=False)

        # --- WinSxS
        card = self._card(right, "Windows Component Store - WinSxS")
        self._desc(card, "Xoá bản cũ của các thành phần Windows còn sót sau khi cập nhật "
                         "(DISM /StartComponentCleanup). An toàn, có thể mất 5-15 phút.")
        self._button(card, "Dọn WinSxS", self._winsxs, admin=True)

        self.set_busy(False)
        self.refresh()

    # ------------------------------------------------------------ dựng giao diện
    def _card(self, parent, title, expand=False):
        frame = ttk.LabelFrame(parent, text=title, padding=10)
        frame.pack(fill="both" if expand else "x", expand=expand, pady=(0, 10))
        return frame

    def _desc(self, parent, text):
        ttk.Label(parent, text=text, style="Muted.TLabel", wraplength=440,
                  justify="left").pack(anchor="w", pady=(4, 8))

    def _button(self, parent, text, command, admin):
        button = ttk.Button(parent, text=text, command=command)
        button.pack(side="left", padx=(0, 6))
        self.buttons.append((button, admin))
        return button

    def set_busy(self, busy):
        for button, needs_admin in self.buttons:
            disabled = busy or (needs_admin and not self.app.admin)
            button.configure(state="disabled" if disabled else "normal")

    def refresh(self):
        fmt = engine.format_size
        h = system.hibernation_info()
        mode = {"off": "Đang tắt", "reduced": "Rút gọn (chỉ Fast Startup)",
                "full": "Bật đầy đủ"}[h["mode"]]
        self.hib_label.configure(text=f"{fmt(h['size'])}  ·  {mode}" if h["size"] else mode)

        p = system.pagefile_info()
        if p["auto"]:
            setting = "Windows tự quản lý"
        elif p["custom_mb"]:
            setting = f"Cố định {fmt(p['custom_mb'] * 2**20)}"
        else:
            setting = "Hệ thống quản lý"
        self.pf_label.configure(text=f"{fmt(p['size'])}  ·  {setting}  ·  RAM {fmt(p['ram'])}")

        self.distros = system.wsl_distros()
        self.wsl_tree.delete(*self.wsl_tree.get_children())
        for i, d in enumerate(self.distros):
            name = d["name"] + ("  (Docker)" if d["docker"] else "")
            self.wsl_tree.insert("", "end", iid=str(i), values=(name, fmt(d["size"]), d["vhd"]),
                                 tags=("docker",) if d["docker"] else ())
        first = next((str(i) for i, d in enumerate(self.distros) if not d["docker"]), None)
        if first:
            self.wsl_tree.selection_set(first)

    # ------------------------------------------------------------ hành động
    def _hibernate(self, mode):
        question = {
            "reduced": "Rút gọn hiberfil.sys?\n\nVẫn giữ Fast Startup nhưng không còn chế độ Hibernate.",
            "off": "Tắt hẳn Hibernate?\n\nhiberfil.sys sẽ bị xoá, mất cả Hibernate và Fast Startup "
                   "(máy khởi động chậm hơn một chút).",
            "full": "Bật lại Hibernate đầy đủ?\n\nhiberfil.sys sẽ lớn khoảng 40% dung lượng RAM.",
        }[mode]
        if messagebox.askyesno("Ngủ đông", question):
            self.app.run_task("Ngủ đông", system.set_hibernation, mode)

    def _pagefile(self, auto):
        size_mb = None
        if not auto:
            try:
                gb = int(self.pf_gb.get())
            except ValueError:
                gb = 0
            if not 2 <= gb <= 64:
                messagebox.showerror("Bộ nhớ ảo", "Nhập dung lượng từ 2 đến 64 GB.")
                return
            size_mb = gb * 1024
        what = "để Windows tự quản lý" if auto else f"đặt cố định {self.pf_gb.get()} GB"
        if messagebox.askyesno("Bộ nhớ ảo", f"Bộ nhớ ảo sẽ được {what}.\n\n"
                               "Thay đổi có hiệu lực sau khi khởi động lại máy. Tiếp tục?"):
            self.app.run_task("Bộ nhớ ảo", system.set_pagefile, size_mb)

    def _winsxs(self):
        if messagebox.askyesno("WinSxS", "Chạy DISM để dọn WinSxS?\n\n"
                               "Có thể mất 5-15 phút, đừng tắt máy trong lúc chạy."):
            self.app.run_task("Dọn WinSxS", system.winsxs_cleanup)

    def _selected_distro(self):
        sel = self.wsl_tree.selection()
        if not sel:
            messagebox.showinfo("WSL", "Hãy chọn một distro trong danh sách.")
            return None
        distro = self.distros[int(sel[0])]
        if distro["docker"]:
            messagebox.showinfo("WSL", "Ổ ảo của Docker Desktop do Docker Desktop quản lý.\n"
                                       "Hãy dùng Docker Desktop → Settings → Resources.")
            return None
        return distro

    def _wsl_compact(self):
        d = self._selected_distro()
        if d and messagebox.askyesno(
                "Thu gọn WSL", f"Thu gọn ổ ảo của {d['name']} ({engine.format_size(d['size'])})?\n\n"
                "WSL sẽ bị tắt: mọi cửa sổ Linux và Docker Desktop sẽ dừng. "
                "Có thể mất vài phút."):
            self.app.run_task("Thu gọn WSL", system.wsl_compact, d)

    def _wsl_move(self):
        d = self._selected_distro()
        if not d:
            return
        dest = filedialog.askdirectory(title=f"Chọn thư mục để chuyển {d['name']} tới",
                                       mustexist=True)
        if not dest:
            return
        dest = os.path.normpath(dest)
        target = os.path.join(dest, d["name"])
        if os.path.normcase(target) == os.path.normcase(os.path.dirname(d["vhd"])):
            messagebox.showinfo("Chuyển WSL", "Distro đã nằm ở thư mục này.")
            return
        if os.path.isdir(target) and os.listdir(target):
            messagebox.showerror("Chuyển WSL", f"Thư mục {target} đã tồn tại và không rỗng.")
            return
        if shutil.disk_usage(dest).free < d["size"] * 1.1:
            messagebox.showerror("Chuyển WSL", "Ổ đích không đủ dung lượng trống.")
            return
        if messagebox.askyesno(
                "Chuyển WSL", f"Chuyển {d['name']} ({engine.format_size(d['size'])}) tới:\n{target}\n\n"
                "Distro sẽ bị tắt trong lúc chuyển. Dữ liệu bên trong được giữ nguyên. "
                "Có thể mất 5-20 phút."):
            self.app.run_task("Chuyển WSL", system.wsl_move, d, dest)


class MemoryPanel(ttk.Frame):
    """Tab "RAM & Khởi động": tiến trình ngốn RAM, thu gọn bộ nhớ, ứng dụng khởi động."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.buttons = []          # [(button, cần_admin)]
        self.procs = {}            # iid -> nhóm tiến trình
        self.startup = []
        self.sorting = {}          # tree -> [cột, đảo ngược, {cột: tiêu đề}, hàm lấy khoá]

        # --- Tổng quan RAM
        head = ttk.Frame(self)
        head.pack(fill="x", pady=(0, 10))
        self.ram_label = ttk.Label(head, style="Big.TLabel")
        self.ram_label.pack(side="left")
        self.ram_detail = ttk.Label(head, style="Muted.TLabel")
        self.ram_detail.pack(side="left", padx=12, pady=(8, 0))
        ttk.Button(head, text="Làm mới", command=self.refresh).pack(side="right")
        self._button(head, "Xoá bộ nhớ chờ" + ("" if app.admin else " (Admin)"),
                     self._purge, admin=True, side="right")
        self._button(head, "Giải phóng RAM", self._trim, admin=False, side="right")
        self.ram_bar = ttk.Progressbar(self, maximum=100)
        self.ram_bar.pack(fill="x", pady=(0, 10))

        cols = ttk.Frame(self)
        cols.pack(fill="both", expand=True)
        left = ttk.Frame(cols)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        right = ttk.Frame(cols)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))

        # --- Tiến trình
        card = ttk.LabelFrame(left, text="Ứng dụng đang chạy (gộp theo tên)", padding=10)
        card.pack(fill="both", expand=True)
        self.proc_tree = self._tree(card, (("name", "Tiến trình", 220, "w"), ("count", "Số", 40, "e"),
                                           ("ram", "RAM", 90, "e"), ("kind", "Loại", 90, "w")),
                                    self._proc_key)
        self.proc_tree.tag_configure("system", foreground="#888")
        row = ttk.Frame(card)
        row.pack(fill="x", pady=(8, 0))
        self._button(row, "Đóng ứng dụng đã chọn", self._kill, admin=False)

        # --- Khởi động cùng Windows
        card = ttk.LabelFrame(right, text="Khởi động cùng Windows", padding=10)
        card.pack(fill="both", expand=True)
        self.start_tree = self._tree(card, (("name", "Ứng dụng", 200, "w"),
                                            ("state", "Trạng thái", 80, "w"),
                                            ("where", "Phạm vi", 150, "w")),
                                    self._startup_key)
        self.start_tree.tag_configure("off", foreground="#888")
        row = ttk.Frame(card)
        row.pack(fill="x", pady=(8, 0))
        self._button(row, "Tắt khởi động", lambda: self._set_startup(False), admin=False)
        self._button(row, "Bật lại", lambda: self._set_startup(True), admin=False)

        self.set_busy(False)
        self._tick()

    # ------------------------------------------------------------ dựng giao diện
    def _tree(self, parent, columns, key):
        """Bảng có thể sắp xếp: nhấn tiêu đề cột để sắp, nhấn lần nữa để đảo chiều.
        `key(iid, cột)` trả về giá trị dùng để so sánh."""
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings")
        self.sorting[tree] = [None, False, {c[0]: c[1] for c in columns}, key]
        for col, text, width, anchor in columns:
            tree.heading(col, text=text, command=lambda t=tree, c=col: self._sort_by(t, c))
            tree.column(col, width=width, anchor=anchor, stretch=(col == "name"))
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return tree

    def _button(self, parent, text, command, admin, side="left"):
        button = ttk.Button(parent, text=text, command=command)
        button.pack(side=side, padx=(0, 6) if side == "left" else (6, 0))
        self.buttons.append((button, admin))
        return button

    def set_busy(self, busy):
        for button, needs_admin in self.buttons:
            disabled = busy or (needs_admin and not self.app.admin)
            button.configure(state="disabled" if disabled else "normal")

    def _sort_by(self, tree, col):
        state = self.sorting[tree]
        if state[0] == col:
            state[1] = not state[1]
        else:
            # Cột số: lớn trước; cột chữ: A -> Z
            state[0], state[1] = col, isinstance(state[3](tree.get_children()[0], col), (int, float))                 if tree.get_children() else False
        self._apply_sort(tree)

    def _apply_sort(self, tree):
        col, reverse, titles, key = self.sorting[tree]
        for c, text in titles.items():
            tree.heading(c, text=text + ((" ▼" if reverse else " ▲") if c == col else ""))
        if col is None:
            return
        items = sorted(tree.get_children(), key=lambda iid: key(iid, col), reverse=reverse)
        for index, iid in enumerate(items):
            tree.move(iid, "", index)

    def _proc_key(self, iid, col):
        g = self.procs[iid]
        return {"name": g["name"].lower(), "count": g["count"], "ram": g["ram"],
                "kind": (g["system"], g["name"].lower())}[col]

    def _startup_key(self, iid, col):
        it = self.startup[int(iid)]
        return {"name": it["name"].lower(), "state": (not it["enabled"], it["name"].lower()),
                "where": (it["where"], it["name"].lower())}[col]

    # ------------------------------------------------------------ dữ liệu
    def _tick(self):
        """Cập nhật thanh RAM mỗi 2 giây khi tab đang hiển thị."""
        if self.winfo_ismapped():
            self._refresh_ram()
        self.after(2000, self._tick)

    def _refresh_ram(self):
        fmt = engine.format_size
        m = memory.memory_info()
        self.ram_label.configure(text=f"{m['load']}%")
        self.ram_detail.configure(text=f"RAM đang dùng {fmt(m['used'])} / {fmt(m['total'])}  ·  "
                                       f"còn trống {fmt(m['avail'])}")
        self.ram_bar["value"] = m["load"]

    def refresh(self):
        self._refresh_ram()
        fmt = engine.format_size

        selected = set(self.proc_tree.selection())
        self.proc_tree.delete(*self.proc_tree.get_children())
        self.procs = {}
        for g in memory.processes():
            if g["ram"] < 5 * 2**20:
                continue
            iid = g["name"].lower()
            self.procs[iid] = g
            self.proc_tree.insert("", "end", iid=iid, tags=("system",) if g["system"] else (),
                                  values=(g["name"], g["count"], fmt(g["ram"]),
                                          "Hệ thống" if g["system"] else "Ứng dụng"))
        self._apply_sort(self.proc_tree)
        self.proc_tree.selection_set([i for i in selected if i in self.procs])

        selected = set(self.start_tree.selection())
        self.start_tree.delete(*self.start_tree.get_children())
        self.startup = memory.startup_items()
        for i, it in enumerate(self.startup):
            self.start_tree.insert("", "end", iid=str(i), tags=() if it["enabled"] else ("off",),
                                   values=(it["name"], "Bật" if it["enabled"] else "Đã tắt",
                                           it["where"]))
        self._apply_sort(self.start_tree)
        self.start_tree.selection_set([i for i in selected if self.start_tree.exists(i)])

    # ------------------------------------------------------------ hành động
    def _trim(self):
        self.app.run_task("Giải phóng RAM", memory.trim_working_sets)

    def _purge(self):
        self.app.run_task("Xoá bộ nhớ chờ", memory.purge_standby)

    def _kill(self):
        groups = [self.procs[i] for i in self.proc_tree.selection() if i in self.procs]
        if not groups:
            messagebox.showinfo("Đóng ứng dụng", "Hãy chọn ứng dụng trong danh sách "
                                                 "(giữ Ctrl để chọn nhiều).")
            return
        blocked = [g["name"] for g in groups if g["system"]]
        if blocked:
            messagebox.showwarning("Đóng ứng dụng", "Không thể đóng tiến trình của Windows "
                                                    "hoặc bị bảo vệ:\n  " + ", ".join(blocked))
            return
        lines = "\n".join(f"  • {g['name']}  ({engine.format_size(g['ram'])})" for g in groups)
        if messagebox.askyesno("Đóng ứng dụng",
                               f"Buộc đóng các ứng dụng sau?\n\n{lines}\n\n"
                               "Dữ liệu CHƯA LƯU trong các ứng dụng này sẽ bị mất.",
                               icon="warning"):
            self.app.run_task("Đóng ứng dụng", memory.kill, groups)

    def _set_startup(self, enabled):
        items = [self.startup[int(i)] for i in self.start_tree.selection()]
        if not items:
            messagebox.showinfo("Khởi động", "Hãy chọn ứng dụng trong danh sách.")
            return
        errors = []
        for it in items:
            try:
                memory.set_startup(it, enabled)
            except RuntimeError as e:
                errors.append(str(e))
        self.refresh()
        if errors:
            messagebox.showerror("Khởi động", "\n".join(errors))
        else:
            self.app.status.configure(
                text=("Đã bật lại: " if enabled else "Đã tắt khởi động: ")
                + ", ".join(it["name"] for it in items))


def run():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # chữ sắc nét trên màn hình HiDPI
    except (AttributeError, OSError):
        pass
    App().mainloop()
