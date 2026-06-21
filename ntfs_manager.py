#!/usr/bin/env python3
"""
NTFS Manager — full-featured macOS NTFS read/write tool
Free alternative to Paragon NTFS. Powered by macFUSE + ntfs-3g.
"""

from __future__ import annotations
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

import settings as settings_mod
from engine import (
    Partition, DiskUsage, DepStatus, DriveWatcher,
    list_ntfs_drives, enable_write, disable_write, eject,
    check_volume, format_volume, format_exfat, rename_volume,
    install_launch_agent, uninstall_launch_agent, launch_agent_installed,
    notify, MountError, find_ntfs3g, macfuse_installed, open_privacy_security,
)
from filebrowser import DualBrowser
from transfer import TransferJob, TransferPanel, TransferQueue, ConflictAction
from setup_wizard import SetupWizard
from widgets import FlatButton

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

C = {
    "bg":      "#1e1e2e",
    "surface": "#2a2a3e",
    "surface2":"#313145",
    "accent":  "#7c6af7",
    "success": "#4ade80",
    "warning": "#facc15",
    "danger":  "#f87171",
    "text":    "#cdd6f4",
    "subtext": "#a6adc8",
    "border":  "#363653",
    "sel":     "#45475a",
    "bar_bg":  "#363653",
}

STATUS_COLORS = {
    "write":     C["success"],
    "readonly":  C["warning"],
    "unmounted": C["subtext"],
}
STATUS_ICONS = {
    "write":     "✏️ ",
    "readonly":  "🔒 ",
    "unmounted": "💾 ",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _btn(parent, text, cmd, bg=C["accent"], fg="white",
         font=("Helvetica Neue", 10, "bold"), pad=(10, 5), **kw):
    return FlatButton(parent, text=text, command=cmd, font=font,
                      bg=bg, fg=fg, padx=pad[0], pady=pad[1], **kw)


def _label(parent, text, font=("Helvetica Neue", 10), fg=C["text"], **kw):
    return tk.Label(parent, text=text, font=font, bg=C["bg"],
                    fg=fg, **kw)


# ---------------------------------------------------------------------------
# Drive row in sidebar
# ---------------------------------------------------------------------------

class DriveRow(tk.Frame):
    def __init__(self, parent, part: Partition, on_click, **kw):
        super().__init__(parent, bg=C["surface"], cursor="hand2", **kw)
        self.part = part
        self._selected = False
        self._build()
        self._bind_recursive(self, lambda _: on_click(part))

    def _bind_recursive(self, widget, cb):
        widget.bind("<Button-1>", cb)
        for child in widget.winfo_children():
            self._bind_recursive(child, cb)

    def _build(self):
        body = tk.Frame(self, bg=C["surface"])
        body.pack(fill="x", padx=10, pady=8)

        icon = STATUS_ICONS.get(self.part.status, "💾 ")
        tk.Label(body, text=icon, font=("Helvetica Neue", 14),
                 bg=C["surface"], fg=C["text"]).pack(side="left", padx=(0, 6))

        info = tk.Frame(body, bg=C["surface"])
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=self.part.name, font=("Helvetica Neue", 12, "bold"),
                 bg=C["surface"], fg=C["text"], anchor="w").pack(anchor="w")
        tk.Label(info, text=f"{self.part.size_str}  {self.part.dev}",
                 font=("Helvetica Neue", 9), bg=C["surface"],
                 fg=C["subtext"], anchor="w").pack(anchor="w")

        # Disk-usage bar (only if mounted)
        usage = self.part.disk_usage()
        if usage:
            bar_bg = tk.Frame(self, bg=C["bar_bg"], height=4)
            bar_bg.pack(fill="x", padx=10, pady=(0, 2))
            bar_bg.pack_propagate(False)

            color = (C["success"] if usage.percent_used < 70
                     else C["warning"] if usage.percent_used < 90
                     else C["danger"])
            bar_fill = tk.Frame(bar_bg, bg=color, height=4)
            # Approximate fill — updated when rendered
            bar_fill.place(relx=0, rely=0, relwidth=usage.percent_used/100, relheight=1)

            detail = tk.Frame(self, bg=C["surface"])
            detail.pack(fill="x", padx=10, pady=(0, 6))
            tk.Label(detail, text=f"{usage.free_str} free / {usage.total_str}",
                     font=("Helvetica Neue", 9), bg=C["surface"],
                     fg=C["subtext"], anchor="w").pack(side="left")
            status_color = STATUS_COLORS.get(self.part.status, C["subtext"])
            tk.Label(detail,
                     text={"write":"WRITE","readonly":"READ ONLY","unmounted":"UNMOUNTED"}
                     .get(self.part.status,""),
                     font=("Helvetica Neue", 8, "bold"),
                     bg=C["surface"], fg=status_color, anchor="e").pack(side="right")

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")

    def set_selected(self, v: bool):
        self._selected = v
        bg = C["sel"] if v else C["surface"]
        self._set_bg_recursive(self, bg)

    def _set_bg_recursive(self, w, bg):
        try: w.configure(bg=bg)
        except Exception: pass
        for child in w.winfo_children():
            self._set_bg_recursive(child, bg)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

class Sidebar(tk.Frame):
    def __init__(self, parent, app: "App", **kw):
        super().__init__(parent, bg=C["bg"], **kw)
        self.app = app
        self._rows: dict[str, DriveRow] = {}
        self._sel_dev: str | None = None
        self._build()

    def _build(self):
        hdr = tk.Frame(self, bg=C["surface"], pady=8, padx=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="NTFS Drives", font=("Helvetica Neue", 11, "bold"),
                 bg=C["surface"], fg=C["accent"]).pack(side="left")

        # Scrollable list
        self._canvas = tk.Canvas(self, bg=C["bg"], highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._inner = tk.Frame(self._canvas, bg=C["bg"])
        self._win = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                         lambda _: self._canvas.configure(
                             scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfig(self._win, width=e.width))
        self._canvas.bind("<Button-1>", self._on_canvas_click)

    def _on_canvas_click(self, event):
        cy = self._canvas.canvasy(event.y)
        for dev, row in self._rows.items():
            ry = row.winfo_y()
            rh = row.winfo_height()
            if ry <= cy <= ry + rh:
                self.app.select_drive(row.part)
                return

    def update_drives(self, parts: list[Partition]):
        current = {p.dev for p in parts}
        for dev in list(self._rows):
            if dev not in current:
                self._rows[dev].destroy()
                del self._rows[dev]

        for p in parts:
            if p.dev in self._rows:
                self._rows[p.dev].destroy()
            row = DriveRow(self._inner, p, on_click=self.app.select_drive)
            row.pack(fill="x")
            self._rows[p.dev] = row
            if p.dev == self._sel_dev:
                row.set_selected(True)

        if not parts:
            for w in self._inner.winfo_children():
                w.destroy()
            self._rows.clear()
            tk.Label(self._inner,
                     text="No NTFS drives\ndetected.\n\nConnect a drive\nand wait…",
                     font=("Helvetica Neue", 10),
                     bg=C["bg"], fg=C["subtext"], justify="center").pack(pady=30)

    def select(self, dev: str | None):
        if self._sel_dev and self._sel_dev in self._rows:
            self._rows[self._sel_dev].set_selected(False)
        self._sel_dev = dev
        if dev and dev in self._rows:
            self._rows[dev].set_selected(True)


# ---------------------------------------------------------------------------
# Toolbar
# ---------------------------------------------------------------------------

class Toolbar(tk.Frame):
    def __init__(self, parent, app: "App", **kw):
        super().__init__(parent, bg=C["surface2"], pady=6, padx=10, **kw)
        self.app = app
        self._btns: dict[str, FlatButton] = {}
        self._build()

    def _build(self):
        specs = [
            ("enable",   "✏️  Enable Write",   self.app.toggle_write,     C["accent"]),
            ("check",    "🔍 Check Volume",     self.app.check_volume,     C["surface"]),
            ("format",   "💽 Format…",            self.app.format_volume,    C["surface"]),
            ("rename_v", "🏷  Rename Volume",   self.app.rename_volume,    C["surface"]),
            ("eject",    "⏏  Eject",           self.app.eject_drive,      C["surface"]),
            ("finder",   "🗂  Show in Finder",  self.app.open_finder,      C["surface"]),
            ("settings", "⚙  Settings",         self.app.open_settings,    C["surface"]),
        ]
        for key, text, cmd, bg in specs:
            b = FlatButton(self, text=text, command=cmd,
                           font=("Helvetica Neue", 11), bg=bg, fg=C["text"],
                           padx=12, pady=6, state="disabled")
            b.pack(side="left", padx=(0, 5))
            self._btns[key] = b

        # Settings always enabled
        self._btns["settings"].config(state="normal")

    def update_for_drive(self, part: Partition | None):
        if part is None:
            for key, b in self._btns.items():
                b.config(state="disabled" if key != "settings" else "normal")
            return

        for key, b in self._btns.items():
            b.config(state="normal")

        if part.status == "write":
            self._btns["enable"].config(text="🔒 Disable Write", bg=C["danger"])
        else:
            self._btns["enable"].config(text="✏️  Enable Write",  bg=C["accent"])

        if not part.mount:
            self._btns["finder"].config(state="disabled")
            self._btns["check"].config(state="disabled")

        if part.status == "write":
            self._btns["check"].config(state="disabled")   # must unmount first


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------

class SettingsWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=C["bg"])
        self.geometry("440x440")
        self.resizable(False, False)
        self.grab_set()
        self._cfg = settings_mod.get()
        self._vars: dict[str, tk.Variable] = {}
        self._build()

    def _build(self):
        tk.Label(self, text="Settings", font=("Helvetica Neue", 16, "bold"),
                 bg=C["bg"], fg=C["text"]).pack(pady=(20, 16))

        frame = tk.Frame(self, bg=C["bg"])
        frame.pack(fill="both", expand=True, padx=24)

        self._section(frame, "Transfer")
        self._check(frame, "prevent_sleep",    "Prevent Mac from sleeping during transfers")
        self._check(frame, "verify_integrity", "Verify file integrity after copy (MD5 checksum)")
        self._radio_row(frame, "conflict_action", "On filename conflict:",
                        [("Rename (safe)", "rename"),
                         ("Overwrite",     "overwrite"),
                         ("Skip",          "skip")])

        self._section(frame, "Notifications")
        self._check(frame, "notify_on_mount",    "Notify when a drive is mounted")
        self._check(frame, "notify_on_complete", "Notify when a transfer completes")

        self._section(frame, "Auto-Mount at Login")
        self._check(frame, "automount_enabled",
                    "Auto-mount NTFS drives when you log in",
                    on_change=self._toggle_automount)

        self._section(frame, "File Browser")
        self._check(frame, "show_hidden_files", "Show hidden files by default")

        # Save / Close
        btn_row = tk.Frame(self, bg=C["bg"])
        btn_row.pack(pady=16)
        _btn(btn_row, "Save & Close", self._save,
             bg=C["accent"]).pack(side="left", padx=6)
        _btn(btn_row, "Cancel", self.destroy,
             bg=C["border"], fg=C["text"]).pack(side="left")

    def _section(self, parent, title):
        tk.Label(parent, text=title, font=("Helvetica Neue", 10, "bold"),
                 bg=C["bg"], fg=C["accent"]).pack(anchor="w", pady=(12, 2))
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(0, 6))

    def _check(self, parent, key, label, on_change=None):
        var = tk.BooleanVar(value=self._cfg[key])
        self._vars[key] = var
        cb = tk.Checkbutton(parent, text=label, variable=var,
                            font=("Helvetica Neue", 10), bg=C["bg"], fg=C["text"],
                            selectcolor=C["surface"], activebackground=C["bg"],
                            relief="flat", bd=0, anchor="w")
        if on_change:
            var.trace_add("write", lambda *_: on_change(var.get()))
        cb.pack(anchor="w", padx=8, pady=1)

    def _radio_row(self, parent, key, label, options):
        tk.Label(parent, text=label, font=("Helvetica Neue", 10),
                 bg=C["bg"], fg=C["text"]).pack(anchor="w", padx=8, pady=(4, 2))
        var = tk.StringVar(value=self._cfg[key])
        self._vars[key] = var
        row = tk.Frame(parent, bg=C["bg"])
        row.pack(anchor="w", padx=16)
        for lbl, val in options:
            tk.Radiobutton(row, text=lbl, variable=var, value=val,
                           font=("Helvetica Neue", 10), bg=C["bg"], fg=C["text"],
                           selectcolor=C["surface"], activebackground=C["bg"],
                           relief="flat", bd=0).pack(side="left", padx=6)

    def _toggle_automount(self, enabled: bool):
        if enabled:
            ok, msg = install_launch_agent()
        else:
            ok, msg = uninstall_launch_agent()
        if not ok:
            messagebox.showerror("Auto-Mount Error", msg, parent=self)

    def _save(self):
        for key, var in self._vars.items():
            self._cfg.set(key, var.get())
        self.destroy()


# ---------------------------------------------------------------------------
# Volume operations dialog (check / format output viewer)
# ---------------------------------------------------------------------------

class VolumeOpDialog(tk.Toplevel):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=C["bg"])
        self.geometry("520x340")
        self.grab_set()

        tk.Label(self, text=title, font=("Helvetica Neue", 14, "bold"),
                 bg=C["bg"], fg=C["text"]).pack(pady=(16, 8))

        self._log = scrolledtext.ScrolledText(
            self, font=("Menlo", 10),
            bg=C["surface"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0,
            state="disabled", height=12,
        )
        self._log.pack(fill="both", expand=True, padx=16, pady=8)

        self._status = tk.Label(self, text="Running…", font=("Helvetica Neue", 10),
                                bg=C["bg"], fg=C["warning"])
        self._status.pack()

        self._close_btn = _btn(self, "Close", self.destroy,
                               bg=C["border"], fg=C["text"])
        self._close_btn.pack(pady=10)
        self._close_btn.config(state="disabled")

    def append(self, text: str):
        self._log.config(state="normal")
        self._log.insert("end", text)
        self._log.see("end")
        self._log.config(state="disabled")

    def done(self, success: bool, summary: str):
        self._status.config(
            text=summary,
            fg=C["success"] if success else C["danger"],
        )
        self._close_btn.config(state="normal")


# ---------------------------------------------------------------------------
# Format picker dialog  (choose exFAT vs NTFS before formatting)
# ---------------------------------------------------------------------------

class FormatPickerDialog(tk.Toplevel):
    """Let the user pick exFAT or NTFS and enter a volume label before formatting."""

    def __init__(self, parent, part_name: str, part_size: str, default_label: str):
        super().__init__(parent)
        self.title("Format Drive")
        self.configure(bg=C["bg"])
        self.geometry("480x400")
        self.resizable(False, False)
        self.grab_set()

        self.result: dict | None = None
        self._fs_var    = tk.StringVar(value="exfat")
        self._label_var = tk.StringVar(value=default_label)
        self._build(part_name, part_size)

    def _build(self, part_name: str, part_size: str):
        # Warning banner
        warn = tk.Frame(self, bg="#3a1f00", pady=8, padx=16)
        warn.pack(fill="x")
        tk.Label(warn,
                 text=f"⚠  FORMAT '{part_name}' ({part_size})  —  ALL DATA WILL BE ERASED",
                 font=("Helvetica Neue", 10, "bold"), bg="#3a1f00", fg=C["warning"],
                 wraplength=440, justify="left").pack(anchor="w")

        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=14)

        tk.Label(body, text="Choose a format:", font=("Helvetica Neue", 12, "bold"),
                 bg=C["bg"], fg=C["text"]).pack(anchor="w", pady=(0, 10))

        # exFAT card
        exfat_card = tk.Frame(body, bg=C["surface"], padx=14, pady=10)
        exfat_card.pack(fill="x", pady=(0, 6))
        tk.Radiobutton(
            exfat_card, text="exFAT  (Recommended for most users)",
            variable=self._fs_var, value="exfat",
            font=("Helvetica Neue", 11, "bold"), bg=C["surface"], fg=C["success"],
            selectcolor=C["surface"], activebackground=C["surface"],
        ).pack(anchor="w")
        tk.Label(
            exfat_card,
            text="Native on Mac, Windows & Linux. Full hardware speed (~400 MB/s).\n"
                 "No extra drivers needed. Best for general file transfer.",
            font=("Helvetica Neue", 9), bg=C["surface"], fg=C["subtext"], justify="left",
        ).pack(anchor="w", padx=20)

        # NTFS card
        ntfs_card = tk.Frame(body, bg=C["surface"], padx=14, pady=10)
        ntfs_card.pack(fill="x", pady=(0, 14))
        tk.Radiobutton(
            ntfs_card, text="NTFS",
            variable=self._fs_var, value="ntfs",
            font=("Helvetica Neue", 11, "bold"), bg=C["surface"], fg=C["text"],
            selectcolor=C["surface"], activebackground=C["surface"],
        ).pack(anchor="w")
        tk.Label(
            ntfs_card,
            text="Windows-native. Required for ACLs/permissions or existing Windows data.\n"
                 "Slower on Mac (~100 MB/s) — needs ntfs-3g for write access.",
            font=("Helvetica Neue", 9), bg=C["surface"], fg=C["subtext"], justify="left",
        ).pack(anchor="w", padx=20)

        # Volume label entry
        lrow = tk.Frame(body, bg=C["bg"])
        lrow.pack(fill="x")
        tk.Label(lrow, text="Volume name:", font=("Helvetica Neue", 10),
                 bg=C["bg"], fg=C["text"]).pack(side="left", padx=(0, 8))
        tk.Entry(lrow, textvariable=self._label_var,
                 font=("Helvetica Neue", 10), bg=C["surface"], fg=C["text"],
                 insertbackground=C["text"], relief="flat", bd=4,
                 width=22).pack(side="left")

        # Buttons
        btn_row = tk.Frame(self, bg=C["bg"])
        btn_row.pack(pady=14)
        _btn(btn_row, "Cancel", self.destroy,
             bg=C["border"], fg=C["text"]).pack(side="left", padx=6)
        _btn(btn_row, "Format…", self._confirm,
             bg=C["danger"]).pack(side="left", padx=6)

    def _confirm(self):
        label = self._label_var.get().strip() or "Untitled"
        self.result = {"fs": self._fs_var.get(), "label": label}
        self.destroy()


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NTFS Manager")
        self.configure(bg=C["bg"])
        self.geometry("1060x680")
        self.minsize(760, 500)

        self._drives:   list[Partition] = []
        self._selected: Partition | None = None
        self._watcher:  DriveWatcher | None = None
        self._cfg = settings_mod.get()

        os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

        self._queue = TransferQueue(
            on_progress=self._on_transfer_progress,
            on_job_done=self._on_transfer_done,
        )

        self._build_menu()
        self._build_ui()
        self._check_deps()
        self._start_watcher()
        self.protocol("WM_DELETE_WINDOW", self._on_quit)
        # On a fresh install, guide the user straight into setup.
        if not DepStatus().ready:
            self.after(400, self._open_setup)

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self):
        menu = tk.Menu(self, bg=C["surface"], fg=C["text"])
        self.config(menu=menu)

        file_m = tk.Menu(menu, tearoff=False, bg=C["surface"], fg=C["text"])
        menu.add_cascade(label="File", menu=file_m)
        file_m.add_command(label="Open in Finder",       command=self.open_finder)
        file_m.add_command(label="New Folder on Drive",  command=self._new_folder_drive)
        file_m.add_separator()
        file_m.add_command(label="Quit", command=self._on_quit, accelerator="⌘Q")
        self.bind_all("<Command-q>", lambda _: self._on_quit())

        drive_m = tk.Menu(menu, tearoff=False, bg=C["surface"], fg=C["text"])
        menu.add_cascade(label="Drive", menu=drive_m)
        drive_m.add_command(label="Enable / Disable Write", command=self.toggle_write)
        drive_m.add_command(label="Check Volume",           command=self.check_volume)
        drive_m.add_command(label="Format…",                 command=self.format_volume)
        drive_m.add_command(label="Rename Volume",          command=self.rename_volume)
        drive_m.add_separator()
        drive_m.add_command(label="Eject",   command=self.eject_drive)
        drive_m.add_command(label="Refresh", command=self.manual_refresh, accelerator="⌘R")
        self.bind_all("<Command-r>", lambda _: self.manual_refresh())

        help_m = tk.Menu(menu, tearoff=False, bg=C["surface"], fg=C["text"])
        menu.add_cascade(label="Help", menu=help_m)
        help_m.add_command(label="Setup Wizard", command=self._open_setup)
        help_m.add_command(label="“App can’t be opened” — how to allow it",
                           command=self._show_gatekeeper_help)
        help_m.add_command(label="Open Privacy & Security Settings",
                           command=open_privacy_security)
        help_m.add_command(label="About",        command=self._about)

    def _show_gatekeeper_help(self):
        if messagebox.askyesno(
            "Allowing NTFS Manager",
            "Because this is a free, unsigned app, macOS blocks it the first time "
            "you open it (“Apple cannot check it for malicious software”).\n\n"
            "To allow it (one time):\n"
            "1. Double-click the app once and click “Done” on the warning.\n"
            "2. Open System Settings → Privacy & Security.\n"
            "3. Scroll down to “NTFS Manager was blocked” and click “Open Anyway”.\n"
            "4. Confirm and enter your password.\n\n"
            "Open Privacy & Security now?",
            parent=self,
        ):
            open_privacy_security()

    # ------------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=C["surface"], pady=10, padx=16)
        hdr.pack(fill="x")
        self._header = hdr
        tk.Label(hdr, text="NTFS Manager",
                 font=("Helvetica Neue", 16, "bold"),
                 bg=C["surface"], fg=C["text"]).pack(side="left")
        self._status_lbl = tk.Label(hdr, text="Starting…",
                                    font=("Helvetica Neue", 10),
                                    bg=C["surface"], fg=C["subtext"])
        self._status_lbl.pack(side="left", padx=14)
        _btn(hdr, "⟳  Refresh", self.manual_refresh,
             bg=C["border"], fg=C["text"], font=("Helvetica Neue", 10),
             pad=(10, 4)).pack(side="right")

        # Dep warning banner
        self._dep_banner = tk.Frame(self, bg="#3a1f00", pady=7, padx=16)
        self._dep_lbl    = tk.Label(self._dep_banner, text="",
                                    font=("Helvetica Neue", 10),
                                    bg="#3a1f00", fg=C["warning"], justify="left")
        self._dep_lbl.pack(side="left")
        _btn(self._dep_banner, "Open Setup Wizard", self._open_setup,
             bg=C["warning"], fg="#000", font=("Helvetica Neue", 10, "bold"),
             pad=(10, 3)).pack(side="right")

        # Main body: sidebar | separator | right
        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True)

        self._sidebar = Sidebar(body, app=self)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.configure(width=240)
        self._sidebar.pack_propagate(False)

        tk.Frame(body, bg=C["border"], width=1).pack(side="left", fill="y")

        right = tk.Frame(body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True)

        # Toolbar
        self._toolbar = Toolbar(right, app=self)
        self._toolbar.pack(fill="x")

        tk.Frame(right, bg=C["border"], height=1).pack(fill="x")

        # Transfer panel (bottom, hidden by default)
        self._xfer_panel = TransferPanel(right, queue=self._queue)

        # File browser
        self._browser = DualBrowser(
            right,
            on_copy=self._start_copy,
            on_move=self._start_move,
        )
        self._browser.pack(fill="both", expand=True)

        # Status bar
        sb = tk.Frame(self, bg=C["surface"], pady=4)
        sb.pack(fill="x", side="bottom")
        tk.Label(sb,
                 text="Free & open-source  •  macFUSE + ntfs-3g",
                 font=("Helvetica Neue", 9),
                 bg=C["surface"], fg=C["subtext"]).pack(side="left", padx=10)
        self._sel_lbl = tk.Label(sb, text="", font=("Helvetica Neue", 9),
                                 bg=C["surface"], fg=C["subtext"])
        self._sel_lbl.pack(side="right", padx=10)

    # ------------------------------------------------------------------
    # Dependency check
    # ------------------------------------------------------------------

    def _check_deps(self):
        dep = DepStatus()
        if not dep.ready:
            self._dep_lbl.config(
                text=f"Missing: {', '.join(dep.missing())} — write access unavailable."
            )
            self._dep_banner.pack(fill="x", after=self._header)
        else:
            self._dep_banner.pack_forget()

    # ------------------------------------------------------------------
    # Drive watcher
    # ------------------------------------------------------------------

    def _start_watcher(self):
        self._watcher = DriveWatcher(
            lambda parts: self.after(0, lambda: self._apply_drives(parts)),
            interval=2.0,
        )
        self._watcher.start()
        self.after(300, self.manual_refresh)

    def manual_refresh(self):
        parts = list_ntfs_drives()
        self._apply_drives(parts)

    def _apply_drives(self, parts: list[Partition]):
        prev_devs   = {p.dev for p in self._drives}
        new_devs    = {p.dev for p in parts}
        arrived     = new_devs - prev_devs
        departed    = prev_devs - new_devs

        self._drives = parts
        self._sidebar.update_drives(parts)
        n = len(parts)
        self._status_lbl.config(
            text=f"{n} NTFS drive{'s' if n != 1 else ''} detected"
        )

        # Notify on plug/unplug
        if self._cfg["notify_on_mount"]:
            for dev in arrived:
                p = next((x for x in parts if x.dev == dev), None)
                if p: notify("NTFS Manager", f"Drive connected: {p.name}")
            for dev in departed:
                notify("NTFS Manager", "A drive was disconnected.", sound=False)

        # Refresh selected drive state
        if self._selected:
            match = next((p for p in parts if p.dev == self._selected.dev), None)
            if match:
                self._selected = match
                self._toolbar.update_for_drive(match)
                self._browser.set_ntfs_writable(match.status == "write")
            else:
                self._selected = None
                self._toolbar.update_for_drive(None)
                self._sel_lbl.config(text="")

    # ------------------------------------------------------------------
    # Drive selection
    # ------------------------------------------------------------------

    def select_drive(self, part: Partition):
        self._selected = part
        self._sidebar.select(part.dev)
        self._toolbar.update_for_drive(part)
        self._sel_lbl.config(text=f"Selected: {part.name}")
        if part.mount:
            self._browser.set_ntfs_root(part.mount, writable=(part.status == "write"))

    # ------------------------------------------------------------------
    # Drive actions
    # ------------------------------------------------------------------

    def toggle_write(self):
        part = self._selected
        if not part: return
        if part.status == "write":
            self._run_in_thread(
                lambda: disable_write(part),
                on_success=self.manual_refresh,
                on_error=self._mount_error,
                busy_msg="Disabling write access…",
            )
        else:
            self._run_in_thread(
                lambda: enable_write(part),
                on_success=self._after_enable_write,
                on_error=self._mount_error,
                busy_msg="Enabling write access…",
            )

    def _after_enable_write(self, mp: str):
        self.manual_refresh()
        if self._selected:
            self._browser.set_ntfs_root(mp, writable=True)
        if self._cfg["notify_on_mount"]:
            notify("NTFS Manager",
                   f"Write enabled: {self._selected.name if self._selected else mp}")

    def check_volume(self):
        part = self._selected
        if not part: return
        if part.status == "write":
            messagebox.showinfo("Check Volume",
                                "Disable write access first (the volume must be unmounted).",
                                parent=self)
            return
        if not messagebox.askyesno(
            "Check Volume",
            f"Run ntfsfix on '{part.name}'?\n"
            "The volume will be temporarily unmounted.",
            parent=self,
        ): return

        dlg = VolumeOpDialog(self, f"Checking: {part.name}")
        dlg.append(f"Running ntfsfix on {part.dev}…\n\n")

        def task():
            ok, out = check_volume(part)
            self.after(0, lambda: dlg.append(out + "\n"))
            self.after(0, lambda: dlg.done(ok, "✓ Volume OK" if ok else "⚠ Errors found"))
            self.after(0, self.manual_refresh)

        threading.Thread(target=task, daemon=True).start()

    def format_volume(self):
        part = self._selected
        if not part: return

        # Step 1: pick format + label
        picker = FormatPickerDialog(self, part.name, part.size_str, part.name)
        self.wait_window(picker)
        if picker.result is None:
            return

        fs       = picker.result["fs"]          # "exfat" | "ntfs"
        label    = picker.result["label"]
        fs_name  = "exFAT" if fs == "exfat" else "NTFS"

        # Step 2: double-confirm (destructive)
        if not messagebox.askyesno(
            f"⚠ Format as {fs_name} — ALL DATA WILL BE LOST",
            f"This will ERASE everything on '{part.name}' ({part.size_str}).\n\n"
            f"Format as {fs_name} with label '{label}'?",
            icon="warning", parent=self,
        ): return
        if not messagebox.askyesno(
            "Final confirmation",
            f"Last chance — permanently erase '{part.name}'?",
            icon="warning", parent=self,
        ): return

        op_dlg = VolumeOpDialog(self, f"Formatting as {fs_name}: {part.name}")
        op_dlg.append(f"Formatting {part.dev} as {fs_name} with label '{label}'…\n\n")

        def task():
            from engine import _run_admin, _q
            if fs == "ntfs":
                # mkntfs needs the volume unmounted first
                _run_admin(f"diskutil unmount {_q(part.dev)} || true")
                ok, out = format_volume(part.dev, label)
            else:
                # diskutil eraseVolume handles unmounting itself
                ok, out = format_exfat(part.dev, label)

            self.after(0, lambda: op_dlg.append(out + "\n"))
            msg = f"✓ {fs_name} format complete" if ok else f"✗ {fs_name} format failed"
            self.after(0, lambda: op_dlg.done(ok, msg))
            if ok and fs == "exfat":
                self.after(0, lambda: op_dlg.append(
                    "\nℹ  Drive reformatted as exFAT — it will no longer appear in\n"
                    "   NTFS Manager, but is accessible natively in Finder at full speed.\n"
                ))
            self.after(0, self.manual_refresh)

        threading.Thread(target=task, daemon=True).start()

    def rename_volume(self):
        part = self._selected
        if not part: return
        from tkinter import simpledialog
        new_name = simpledialog.askstring(
            "Rename Volume", "New volume name:",
            initialvalue=part.name, parent=self,
        )
        if not new_name or new_name == part.name: return

        def task():
            ok, msg = rename_volume(part, new_name)
            self.after(0, lambda: (
                messagebox.showinfo("Rename", msg, parent=self)
                if ok else
                messagebox.showerror("Rename Error", msg, parent=self)
            ))
            self.after(0, self.manual_refresh)

        threading.Thread(target=task, daemon=True).start()

    def open_finder(self):
        part = self._selected
        if part and part.mount:
            subprocess.Popen(["open", part.mount])

    def eject_drive(self):
        part = self._selected
        if not part: return
        if not messagebox.askyesno(
            "Eject Drive",
            f"Eject '{part.name}'?\nEnsure all transfers are complete.",
            parent=self,
        ): return

        def task():
            try:
                eject(part)
                self.after(0, self.manual_refresh)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Eject Error", str(e), parent=self))

        threading.Thread(target=task, daemon=True).start()

    def _mount_error(self, e: Exception):
        self._status_lbl.config(text="Error.")
        messagebox.showerror("Error", str(e), parent=self)

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _new_folder_drive(self):
        self._browser.right.new_folder()

    def _start_copy(self, sources: list[str], dest: str):
        self._enqueue(sources, dest, move=False)

    def _start_move(self, sources: list[str], dest: str):
        self._enqueue(sources, dest, move=True)

    def _enqueue(self, sources: list[str], dest: str, move: bool):
        if not sources: return
        if not os.path.isdir(dest):
            messagebox.showerror("Error", f"Not a directory:\n{dest}", parent=self)
            return

        conflict_map = {"rename": ConflictAction.RENAME,
                        "overwrite": ConflictAction.OVERWRITE,
                        "skip": ConflictAction.SKIP}
        job = TransferJob(
            sources=sources, destination=dest, move=move,
            conflict=conflict_map.get(self._cfg["conflict_action"], ConflictAction.RENAME),
            verify=self._cfg["verify_integrity"],
        )
        self._xfer_panel.show_for_job(job)
        self._queue.enqueue(job)

    def _on_transfer_progress(self, job: TransferJob, stats):
        self._xfer_panel.notify_progress(job, stats)

    def _on_transfer_done(self, job: TransferJob, stats):
        self._xfer_panel.notify_done(job, stats)
        self.after(600, self._browser.refresh_left)
        self.after(600, self._browser.refresh_right)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _run_in_thread(self, task, on_success=None, on_error=None, busy_msg="Working…"):
        self._status_lbl.config(text=busy_msg)

        def worker():
            try:
                result = task()
                if on_success:
                    self.after(0, lambda: on_success(result) if result is not None
                               else on_success())
            except Exception as e:
                if on_error:
                    self.after(0, lambda err=e: on_error(err))

        threading.Thread(target=worker, daemon=True).start()

    def open_settings(self):
        SettingsWindow(self)

    def _open_setup(self):
        SetupWizard(self, on_complete=self._check_deps)

    def _about(self):
        messagebox.showinfo(
            "About NTFS Manager",
            "NTFS Manager  —  Free NTFS read/write for macOS\n\n"
            "• Full read/write access via macFUSE + ntfs-3g\n"
            "• Optimised mount flags for maximum ntfs-3g throughput\n"
            "• Format drives as NTFS or exFAT (native, full speed)\n"
            "• Built-in dual-pane file browser\n"
            "• Copy / move with progress, speed, ETA, cancel\n"
            "• File integrity verification (MD5)\n"
            "• Volume check (ntfsfix)\n"
            "• Auto-mount at login via LaunchAgent\n"
            "• macOS notifications\n"
            "• Prevents sleep during large transfers\n\n"
            "100% free. No telemetry. No subscription.",
            parent=self,
        )

    def _on_quit(self):
        if self._watcher:
            self._watcher.stop()
        if self._queue.current:
            if not messagebox.askyesno(
                "Quit",
                "A file transfer is in progress. Cancel it and quit?",
                parent=self,
            ): return
            self._queue.cancel_current()
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if sys.platform != "darwin":
        print("NTFS Manager is macOS-only.")
        sys.exit(1)

    app = App()
    app.mainloop()
