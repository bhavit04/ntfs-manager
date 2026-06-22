"""
transfer.py — threaded file copy/move with progress, integrity check,
              caffeinate (sleep prevention), partial-file cleanup, job queue.
"""

from __future__ import annotations
import hashlib
import os
import shutil
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from enum import Enum, auto
from queue import Queue
from typing import Callable, List, Optional

import settings as settings_mod
from widgets import FlatButton


class ConflictAction(Enum):
    SKIP      = auto()
    OVERWRITE = auto()
    RENAME    = auto()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class TransferStats:
    total_bytes:  int   = 0
    copied_bytes: int   = 0
    total_files:  int   = 0
    done_files:   int   = 0
    current_file: str   = ""
    speed_bps:    float = 0.0
    elapsed:      float = 0.0
    cancelled:    bool  = False
    error:        Optional[str] = None
    finished:     bool  = False
    verify_fail:  Optional[str] = None   # file that failed checksum

    @property
    def percent(self) -> float:
        return min(100.0, 100.0 * self.copied_bytes / self.total_bytes) if self.total_bytes else 0.0

    @property
    def eta_str(self) -> str:
        if self.speed_bps <= 0: return "--:--"
        r = int((self.total_bytes - self.copied_bytes) / self.speed_bps)
        h, rem = divmod(r, 3600)
        m, s   = divmod(rem, 60)
        return f"{h}h {m}m" if h else f"{m}m {s}s"

    @property
    def speed_str(self) -> str:
        b = self.speed_bps
        for u in ("B/s", "KB/s", "MB/s", "GB/s"):
            if b < 1024: return f"{b:.1f} {u}"
            b /= 1024
        return f"{b:.1f} TB/s"


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

_CHUNK = 4 * 1024 * 1024  # 4 MB


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk: break
            h.update(chunk)
    return h.hexdigest()


def _unique_name(dst: str) -> str:
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 1
    while True:
        c = f"{base} ({i}){ext}"
        if not os.path.exists(c): return c
        i += 1


def _collect_files(paths: list[str]) -> list[tuple[str, int]]:
    result = []
    for p in paths:
        if os.path.isfile(p):          # isfile follows symlinks → False for broken ones
            result.append((p, os.path.getsize(p)))
        elif os.path.isdir(p):
            for root, _, files in os.walk(p, followlinks=False):
                for f in files:
                    fp = os.path.join(root, f)
                    if not os.path.isfile(fp):   # skip broken symlinks
                        continue
                    try:   result.append((fp, os.path.getsize(fp)))
                    except OSError: pass         # skip unreadable files
    return result


# ---------------------------------------------------------------------------
# Caffeinate (prevent sleep)
# ---------------------------------------------------------------------------

class _Caffeinate:
    """Wrap macOS `caffeinate` to prevent sleep during long transfers."""
    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None

    def start(self):
        cfg = settings_mod.get()
        if cfg["prevent_sleep"] and self._proc is None:
            try:
                self._proc = subprocess.Popen(
                    ["caffeinate", "-dims"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass

    def stop(self):
        if self._proc:
            self._proc.terminate()
            self._proc = None


# ---------------------------------------------------------------------------
# Transfer job
# ---------------------------------------------------------------------------

class TransferJob:
    def __init__(
        self,
        sources:     list[str],
        destination: str,
        move:        bool           = False,
        conflict:    ConflictAction = ConflictAction.RENAME,
        verify:      bool           = False,
        on_progress: Optional[Callable[[TransferStats], None]] = None,
        on_done:     Optional[Callable[[TransferStats], None]] = None,
    ):
        self.sources     = sources
        self.destination = destination
        self.move        = move
        self.conflict    = conflict
        self.verify      = verify
        self.stats       = TransferStats()
        self.transferred: list[tuple[str, str]] = []   # (src, dst) for undo
        self._on_progress = on_progress or (lambda _: None)
        self._on_done     = on_done     or (lambda _: None)
        self._cancel      = threading.Event()
        self._thread      = threading.Thread(target=self._run, daemon=True)
        self._caff        = _Caffeinate()

    def start(self):
        self._caff.start()
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    # ------------------------------------------------------------------

    def _sample_speed(self, force: bool = False):
        """Update speed_bps on a steady ~0.5s cadence, smoothed with an EMA.

        FUSE-T writes through an NFS layer that buffers then flushes in bursts,
        so the raw per-window rate swings wildly between KB/s and MB/s. An
        exponential moving average (time constant ~2s) gives a stable number.
        """
        now = time.monotonic()
        dt  = now - self._spd_t0
        if dt >= 0.5:
            inst = (self.stats.copied_bytes - self._spd_bytes0) / dt
            self._spd_ema = inst if self._spd_ema <= 0 \
                else self._spd_ema + 0.25 * (inst - self._spd_ema)
            self.stats.speed_bps = self._spd_ema
            self._spd_t0, self._spd_bytes0 = now, self.stats.copied_bytes
            force = True                      # rate changed → push an update
        if force:
            self.stats.elapsed = now - self._t0
            self._on_progress(self.stats)

    def _run(self):
        t0 = time.monotonic()
        self._t0          = t0
        self._spd_t0      = t0
        self._spd_bytes0  = 0
        self._spd_ema     = 0.0

        all_files = _collect_files(self.sources)
        self.stats.total_files = len(all_files)
        self.stats.total_bytes = sum(s for _, s in all_files)
        self._on_progress(self.stats)

        for src, _ in all_files:
            if self._cancel.is_set():
                break

            # Build destination path
            rel = None
            for orig in self.sources:
                orig_real = os.path.realpath(orig)
                src_real  = os.path.realpath(src)
                if os.path.isdir(orig_real) and src_real.startswith(orig_real + os.sep):
                    rel = os.path.relpath(src_real, os.path.dirname(orig_real))
                    break
            if rel is None:
                rel = os.path.basename(src)

            dst = os.path.join(self.destination, rel)

            if os.path.exists(dst):
                if self.conflict == ConflictAction.SKIP:
                    self.stats.done_files += 1
                    continue
                elif self.conflict == ConflictAction.RENAME:
                    dst = _unique_name(dst)
                # OVERWRITE: proceed

            self.stats.current_file = os.path.basename(src)
            self._on_progress(self.stats)

            ok = False
            try:
                ok = self._copy_chunked(src, dst)
            except Exception as e:
                self.stats.error = f"{os.path.basename(src)}: {e}"
                self._on_progress(self.stats)
                self.stats.error = None
                # Remove partial file
                if os.path.exists(dst):
                    try: os.remove(dst)
                    except OSError: pass
                continue

            if not ok:
                # Cancelled mid-copy — clean up partial file
                if os.path.exists(dst):
                    try: os.remove(dst)
                    except OSError: pass
                break

            # Integrity check
            if self.verify:
                try:
                    if _md5(src) != _md5(dst):
                        self.stats.verify_fail = os.path.basename(src)
                        self._on_progress(self.stats)
                        self.stats.verify_fail = None
                except Exception:
                    pass

            if self.move:
                try: os.remove(src)
                except OSError: pass

            self.transferred.append((src, dst))
            self.stats.done_files += 1

            self._sample_speed(force=True)

        self._caff.stop()
        self.stats.finished  = True
        self.stats.cancelled = self._cancel.is_set()
        self._on_done(self.stats)

    def _copy_chunked(self, src: str, dst: str) -> bool:
        os.makedirs(os.path.dirname(dst), exist_ok=True)

        # Overlap reading and writing: a background reader fills a small bounded
        # queue while this thread writes. FUSE-T/NFS writes stall periodically on
        # flushes; reading ahead keeps the write pipeline full during those
        # stalls instead of idling, which lifts and steadies large-file
        # throughput. Bounded queue caps read-ahead memory at maxsize × _CHUNK.
        q: "Queue[Optional[bytes]]" = Queue(maxsize=4)
        read_err: list[BaseException] = []

        def _reader():
            try:
                with open(src, "rb") as fin:
                    while not self._cancel.is_set():
                        chunk = fin.read(_CHUNK)
                        if not chunk:
                            break
                        q.put(chunk)
            except BaseException as e:        # surfaced to the writer below
                read_err.append(e)
            finally:
                q.put(None)                   # sentinel: no more chunks

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        sentinel_seen = False
        try:
            with open(dst, "wb") as fout:
                while True:
                    chunk = q.get()
                    if chunk is None:
                        sentinel_seen = True
                        break
                    if self._cancel.is_set():
                        return False
                    fout.write(chunk)
                    self.stats.copied_bytes += len(chunk)
                    self._sample_speed()      # mid-file refresh for large files
        finally:
            if not sentinel_seen:
                # Drain so a reader parked on a full queue can reach its sentinel.
                while q.get() is not None:
                    pass
            reader.join(timeout=5)

        if read_err:
            raise read_err[0]
        if self._cancel.is_set():
            return False

        try: shutil.copystat(src, dst)
        except OSError: pass
        return True


# ---------------------------------------------------------------------------
# Transfer queue  (ordered list of jobs, runs one at a time)
# ---------------------------------------------------------------------------

class TransferQueue:
    def __init__(self,
                 on_progress: Optional[Callable[[TransferJob, TransferStats], None]] = None,
                 on_job_done: Optional[Callable[[TransferJob, TransferStats], None]] = None):
        self._on_progress = on_progress or (lambda j, s: None)
        self._on_job_done = on_job_done or (lambda j, s: None)
        self._queue: list[TransferJob] = []
        self._lock  = threading.Lock()
        self._running = False
        self._current: Optional[TransferJob] = None

    def enqueue(self, job: TransferJob):
        with self._lock:
            self._queue.append(job)
        if not self._running:
            self._start_next()

    def cancel_current(self):
        if self._current:
            self._current.cancel()

    def _start_next(self):
        with self._lock:
            if not self._queue:
                self._running = False
                self._current = None
                return
            job = self._queue.pop(0)

        self._running = True
        self._current = job

        job._on_progress = lambda s: self._on_progress(job, s)
        job._on_done     = lambda s: self._job_done(job, s)
        job.start()

    def _job_done(self, job: TransferJob, stats: TransferStats):
        self._on_job_done(job, stats)
        self._start_next()

    @property
    def pending(self) -> int:
        return len(self._queue)

    @property
    def current(self) -> Optional[TransferJob]:
        return self._current


# ---------------------------------------------------------------------------
# Progress panel widget
# ---------------------------------------------------------------------------

_C = {
    "bg":      "#1e1e2e",
    "surface": "#2a2a3e",
    "accent":  "#7c6af7",
    "success": "#4ade80",
    "warning": "#facc15",
    "danger":  "#f87171",
    "text":    "#cdd6f4",
    "subtext": "#a6adc8",
    "bar_bg":  "#363653",
}


class TransferPanel(tk.Frame):
    def __init__(self, parent, queue: Optional[TransferQueue] = None, **kwargs):
        super().__init__(parent, bg=_C["surface"], **kwargs)
        self._queue = queue
        self._build()
        self.hide()

    def _build(self):
        top = tk.Frame(self, bg=_C["surface"], pady=6, padx=12)
        top.pack(fill="x")

        self._op_label = tk.Label(top, text="", font=("Helvetica Neue", 11, "bold"),
                                  bg=_C["surface"], fg=_C["text"], anchor="w")
        self._op_label.pack(side="left")

        self._queue_lbl = tk.Label(top, text="", font=("Helvetica Neue", 9),
                                   bg=_C["surface"], fg=_C["subtext"])
        self._queue_lbl.pack(side="left", padx=8)

        self._cancel_btn = FlatButton(
            top, text="Cancel", font=("Helvetica Neue", 11, "bold"),
            bg=_C["danger"], fg="white", padx=12, pady=4, command=self._cancel,
        )
        self._cancel_btn.pack(side="right")

        bar_f = tk.Frame(self, bg=_C["surface"], padx=12)
        bar_f.pack(fill="x")
        self._bar = tk.Canvas(bar_f, height=8, bg=_C["bar_bg"],
                              highlightthickness=0, bd=0)
        self._bar.pack(fill="x", pady=(0, 4))
        self._bar_rect = self._bar.create_rectangle(0, 0, 0, 8,
                                                    fill=_C["accent"], outline="")

        stats_r = tk.Frame(self, bg=_C["surface"], padx=12, pady=4)
        stats_r.pack(fill="x")
        self._file_lbl = tk.Label(stats_r, text="", font=("Helvetica Neue", 10),
                                  bg=_C["surface"], fg=_C["subtext"], anchor="w")
        self._file_lbl.pack(side="left")
        self._stats_lbl = tk.Label(stats_r, text="", font=("Helvetica Neue", 10),
                                   bg=_C["surface"], fg=_C["subtext"], anchor="e")
        self._stats_lbl.pack(side="right")

    # ------------------------------------------------------------------

    def attach_queue(self, queue: TransferQueue):
        self._queue = queue

    def notify_progress(self, job: TransferJob, stats: TransferStats):
        self.after(0, lambda: self._update(job, stats))

    def notify_done(self, job: TransferJob, stats: TransferStats):
        self.after(0, lambda: self._done(job, stats))

    def show_for_job(self, job: TransferJob):
        op = "Moving" if job.move else "Copying"
        self._op_label.config(text=f"{op} files…", fg=_C["text"])
        self._cancel_btn.config(state="normal")
        self.show()

    def _cancel(self):
        if self._queue:
            self._queue.cancel_current()
        self._op_label.config(text="Cancelling…")
        self._cancel_btn.config(state="disabled")

    def _update(self, job: TransferJob, stats: TransferStats):
        self.show()
        w = self._bar.winfo_width()
        self._bar.coords(self._bar_rect, 0, 0, int(w * stats.percent / 100), 8)
        pending = self._queue.pending if self._queue else 0
        self._queue_lbl.config(text=f"+{pending} queued" if pending else "")
        self._file_lbl.config(text=f"  {stats.current_file}"
                                    f"  ({stats.done_files}/{stats.total_files})")
        warn = f"  ⚠ checksum fail: {stats.verify_fail}" if stats.verify_fail else ""
        self._stats_lbl.config(text=f"{stats.percent:.0f}%  {stats.speed_str}"
                                     f"  ETA {stats.eta_str}{warn}  ")

    def _done(self, job: TransferJob, stats: TransferStats):
        from engine import notify
        from settings import get as get_cfg
        if get_cfg()["notify_on_complete"]:
            op = "Move" if job.move else "Copy"
            msg = (f"{op} cancelled." if stats.cancelled
                   else f"{op} complete — {stats.done_files} files.")
            notify("NTFS Manager", msg, sound=not stats.cancelled)

        color = _C["warning"] if stats.cancelled else _C["success"]
        self._op_label.config(
            text="Cancelled." if stats.cancelled else "Transfer complete!",
            fg=color,
        )
        w = self._bar.winfo_width()
        self._bar.coords(self._bar_rect, 0, 0, w, 8)
        self._bar.itemconfig(self._bar_rect, fill=color)
        self._cancel_btn.config(state="disabled")
        self.after(4000, self.hide)

    def show(self):
        self.pack(fill="x", side="bottom")

    def hide(self):
        self.pack_forget()
        self._cancel_btn.config(state="normal")
        self._bar.coords(self._bar_rect, 0, 0, 0, 8)
        self._bar.itemconfig(self._bar_rect, fill=_C["accent"])
        self._op_label.config(text="", fg=_C["text"])
        self._file_lbl.config(text="")
        self._stats_lbl.config(text="")
