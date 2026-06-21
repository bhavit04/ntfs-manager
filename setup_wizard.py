"""
setup_wizard.py — in-app setup wizard for macFUSE + ntfs-3g
"""

from __future__ import annotations
import os
import subprocess
import threading
import tkinter as tk
from tkinter import scrolledtext
from typing import Callable, Optional
from engine import (
    DepStatus, find_ntfs3g, macfuse_installed, fuset_installed,
    sudoers_active, fuse_t_linked, configure_privileges, brew_path, askpass_path,
)
from widgets import FlatButton

COLORS = {
    "bg": "#1e1e2e",
    "surface": "#2a2a3e",
    "surface2": "#313145",
    "accent": "#7c6af7",
    "success": "#4ade80",
    "warning": "#facc15",
    "danger": "#f87171",
    "text": "#cdd6f4",
    "subtext": "#a6adc8",
    "border": "#363653",
    "code_bg": "#12121f",
}


class SetupWizard(tk.Toplevel):
    """
    Modal setup window.  Calls `on_complete()` when deps are ready.
    """

    def __init__(self, parent, on_complete: Optional[Callable] = None):
        super().__init__(parent)
        self.title("NTFS Manager — Setup")
        self.configure(bg=COLORS["bg"])
        self.geometry("580x680")
        self.resizable(False, False)
        self.grab_set()  # modal
        self._on_complete = on_complete or (lambda: None)
        self._build()
        self._check_deps()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build(self):
        # Title
        tk.Label(
            self, text="First-time Setup",
            font=("Helvetica Neue", 18, "bold"),
            bg=COLORS["bg"], fg=COLORS["text"],
        ).pack(pady=(20, 4))

        tk.Label(
            self,
            text="NTFS Manager needs a few free open-source tools to enable write access.",
            font=("Helvetica Neue", 11),
            bg=COLORS["bg"], fg=COLORS["subtext"],
        ).pack(pady=(0, 16))

        # Step cards
        steps_frame = tk.Frame(self, bg=COLORS["bg"])
        steps_frame.pack(fill="x", padx=24)

        self._step1 = StepCard(steps_frame, number="1",
                               title="FUSE-T",
                               desc="Kext-free FUSE for macOS 26 — mounts the drive without a kernel extension.")
        self._step1.pack(fill="x", pady=(0, 8))

        self._step2 = StepCard(steps_frame, number="2",
                               title="ntfs-3g",
                               desc="Open-source NTFS read/write driver.")
        self._step2.pack(fill="x", pady=(0, 8))

        self._step3 = StepCard(steps_frame, number="3",
                               title="Permissions",
                               desc="A one-time scoped sudo rule so mounting never needs a password popup.")
        self._step3.pack(fill="x", pady=(0, 12))

        # Info note
        note = tk.Frame(self, bg="#102a1f", pady=10, padx=14)
        note.pack(fill="x", padx=24)
        tk.Label(
            note,
            text="ℹ  FUSE-T is kext-free, so no reboot or Security approval is needed.\n"
                 "   You'll be asked for your Mac password to install the tools and\n"
                 "   set up the permission rule. That's it.",
            font=("Helvetica Neue", 10),
            bg="#102a1f", fg=COLORS["success"],
            justify="left",
        ).pack(anchor="w")

        # Log area
        self._log = scrolledtext.ScrolledText(
            self, height=7, font=("Menlo", 9),
            bg=COLORS["code_bg"], fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat", bd=0, state="disabled",
        )
        self._log.pack(fill="x", padx=24, pady=12)

        # Buttons
        btn_row = tk.Frame(self, bg=COLORS["bg"])
        btn_row.pack(pady=(0, 20))

        self._install_btn = FlatButton(
            btn_row, text="Install Missing Tools",
            font=("Helvetica Neue", 12, "bold"),
            bg=COLORS["accent"], fg="white",
            padx=18, pady=9,
            command=self._start_install,
        )
        self._install_btn.pack(side="left", padx=8)

        self._done_btn = FlatButton(
            btn_row, text="Re-check",
            font=("Helvetica Neue", 12),
            bg=COLORS["surface"], fg=COLORS["text"],
            padx=18, pady=9,
            command=self._check_deps,
        )
        self._done_btn.pack(side="left", padx=8)

    # ------------------------------------------------------------------
    # Logic
    # ------------------------------------------------------------------

    def _log_write(self, text: str):
        self._log.config(state="normal")
        self._log.insert("end", text)
        self._log.see("end")
        self._log.config(state="disabled")

    def _check_deps(self):
        dep = DepStatus()
        # FUSE-T + macFUSE framework together make the mount layer work.
        self._step1.set_status("ok" if (dep.fuset and dep.macfuse and dep.linked) else "missing")
        self._step2.set_status("ok" if dep.ntfs3g else "missing")
        self._step3.set_status("ok" if dep.sudoers else "missing")

        if dep.ready:
            self._install_btn.config(state="disabled", text="All set ✓",
                                     bg=COLORS["success"], fg="#000")
            self._log_write("✓ Everything is installed and configured. You're ready to go.\n")
            self.after(1500, lambda: (self._on_complete(), self.destroy()))
        elif not dep.brew:
            self._log_write(
                "Homebrew not found!\n"
                "Install it from https://brew.sh first, then re-open this wizard.\n"
            )
            self._install_btn.config(state="disabled", text="Homebrew required")
        else:
            missing = dep.missing()
            self._install_btn.config(
                state="normal",
                text=f"Set up {' + '.join(missing)}",
                bg=COLORS["accent"], fg="white",
            )

    def _start_install(self):
        self._install_btn.config(state="disabled", text="Setting up…")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self):
        dep = DepStatus()

        # macFUSE framework must exist BEFORE ntfs-3g (ntfs-3g-mac requires it).
        if not dep.fuset:
            self._log_async("Installing FUSE-T…\n")
            self._brew_install("macos-fuse-t/homebrew-cask/fuse-t")

        if not macfuse_installed():
            self._log_async("Installing macFUSE framework (this may take a minute)…\n")
            self._brew_install("--cask macfuse")

        if not find_ntfs3g():
            self._log_async("Installing ntfs-3g…\n")
            self._brew_install("gromgit/fuse/ntfs-3g-mac")

        # Always ensure the permission rule AND that ntfs-3g points at FUSE-T's lib
        # (macFUSE's installer overwrites it, which would force the kext). One admin
        # call fixes both.
        if not (sudoers_active() and fuse_t_linked()):
            self._log_async("Configuring permissions & FUSE-T link — approve the password prompt…\n")
            ok, msg = configure_privileges()
            self._log_async(("✓ " if ok else "✗ ") + msg + "\n")

        self.after(0, self._check_deps)

    def _brew_install(self, args: str):
        brew = brew_path()
        if not brew:
            self._log_async("Homebrew not found — install it from https://brew.sh\n")
            return
        env = dict(os.environ)
        env["SUDO_ASKPASS"] = askpass_path()       # GUI password dialog for sudo
        env["HOMEBREW_NO_AUTO_UPDATE"] = "1"        # faster, less noise
        env["HOMEBREW_NO_ENV_HINTS"] = "1"
        proc = subprocess.Popen(
            [brew, "install", *args.split()],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        for line in proc.stdout:
            self.after(0, lambda l=line: self._log_write(l))
        proc.wait()

    def _log_async(self, text: str):
        self.after(0, lambda: self._log_write(text))


# ---------------------------------------------------------------------------
# Step card widget
# ---------------------------------------------------------------------------

class StepCard(tk.Frame):
    _STATUS_CFG = {
        "pending":  {"icon": "○", "color": "#6c7086"},
        "ok":       {"icon": "✓", "color": "#4ade80"},
        "missing":  {"icon": "✗", "color": "#f87171"},
        "working":  {"icon": "…", "color": "#facc15"},
    }

    def __init__(self, parent, number: str, title: str, desc: str, **kwargs):
        super().__init__(parent, bg=COLORS["surface"], pady=10, padx=14, **kwargs)
        self._build(number, title, desc)

    def _build(self, number, title, desc):
        left = tk.Frame(self, bg=COLORS["surface"])
        left.pack(side="left")

        self._num = tk.Label(
            left, text=number,
            font=("Helvetica Neue", 18, "bold"),
            bg=COLORS["surface"], fg=COLORS["accent"],
            width=2,
        )
        self._num.pack()

        info = tk.Frame(self, bg=COLORS["surface"])
        info.pack(side="left", fill="x", expand=True, padx=(10, 0))

        tk.Label(info, text=title, font=("Helvetica Neue", 12, "bold"),
                 bg=COLORS["surface"], fg=COLORS["text"], anchor="w").pack(anchor="w")
        tk.Label(info, text=desc, font=("Helvetica Neue", 10),
                 bg=COLORS["surface"], fg=COLORS["subtext"],
                 wraplength=360, anchor="w", justify="left").pack(anchor="w")

        self._status_lbl = tk.Label(
            self, text="○", font=("Helvetica Neue", 18),
            bg=COLORS["surface"], fg=COLORS["subtext"],
        )
        self._status_lbl.pack(side="right", padx=8)

    def set_status(self, status: str):
        cfg = self._STATUS_CFG.get(status, self._STATUS_CFG["pending"])
        self._status_lbl.config(text=cfg["icon"], fg=cfg["color"])
