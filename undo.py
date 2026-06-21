"""
undo.py — undo/redo stack for file operations (delete, move, copy, rename,
          new folder). Deletes route through a recoverable trash folder so
          they can be reversed; the trash is purged on a clean quit.
"""

from __future__ import annotations
import glob
import os
import shutil
import uuid
from typing import Callable, List, Tuple

TRASH_NAME = ".ntfsmgr_trash"


# ---------------------------------------------------------------------------
# Trash helpers (keep deleted items on the same volume = fast atomic move)
# ---------------------------------------------------------------------------

def _trash_dir_for(path: str) -> str:
    rp = os.path.realpath(path)
    if rp.startswith("/Volumes/"):
        parts = rp.split(os.sep)               # ['', 'Volumes', 'NAME', ...]
        base = os.sep.join(parts[:3]) if len(parts) >= 3 else os.path.expanduser("~")
    else:
        base = os.path.expanduser("~")
    d = os.path.join(base, TRASH_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _move_to_trash(path: str) -> str:
    d = _trash_dir_for(path)
    name = os.path.basename(path.rstrip("/")) or "item"
    dest = os.path.join(d, f"{uuid.uuid4().hex}__{name}")
    shutil.move(path, dest)
    return dest


def _restore(trash_path: str, original: str):
    os.makedirs(os.path.dirname(original), exist_ok=True)
    shutil.move(trash_path, original)


def purge_trashes():
    """Empty every .ntfsmgr_trash (home + each mounted volume). Best-effort."""
    bases = [os.path.expanduser("~")] + glob.glob("/Volumes/*")
    for base in bases:
        d = os.path.join(base, TRASH_NAME)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Operation + manager
# ---------------------------------------------------------------------------

class Op:
    def __init__(self, label: str, undo_fn: Callable, redo_fn: Callable):
        self.label = label
        self._undo = undo_fn
        self._redo = redo_fn

    def undo(self): self._undo()
    def redo(self): self._redo()


class UndoManager:
    def __init__(self, on_change: Callable[[], None] | None = None):
        self._undo: List[Op] = []
        self._redo: List[Op] = []
        self._on_change = on_change or (lambda: None)

    def push(self, op: Op):
        self._undo.append(op)
        self._redo.clear()
        self._on_change()

    def can_undo(self) -> bool: return bool(self._undo)
    def can_redo(self) -> bool: return bool(self._redo)
    def undo_label(self) -> str: return self._undo[-1].label if self._undo else ""
    def redo_label(self) -> str: return self._redo[-1].label if self._redo else ""

    def undo(self):
        if not self._undo:
            return
        op = self._undo[-1]
        op.undo()                              # may raise → leave stacks intact
        self._undo.pop()
        self._redo.append(op)
        self._on_change()

    def redo(self):
        if not self._redo:
            return
        op = self._redo[-1]
        op.redo()
        self._redo.pop()
        self._undo.append(op)
        self._on_change()


# ---------------------------------------------------------------------------
# Operation factories
# ---------------------------------------------------------------------------

def perform_delete(originals: List[str]) -> Op:
    """Delete now (move to trash) and return an Op that can restore them."""
    pairs: List[Tuple[str, str]] = []          # (original, trash)
    for p in originals:
        pairs.append((p, _move_to_trash(p)))

    def undo():
        for orig, t in pairs:
            _restore(t, orig)

    def redo():
        for i, (orig, _) in enumerate(pairs):
            pairs[i] = (orig, _move_to_trash(orig))

    n = len(pairs)
    return Op(f"Delete {n} item{'s' if n != 1 else ''}", undo, redo)


def record_move(transferred: List[Tuple[str, str]]) -> Op:
    """Op for an already-completed move (list of (src, dst) pairs)."""
    pairs = list(transferred)

    def undo():
        for src, dst in pairs:
            if os.path.exists(dst):
                os.makedirs(os.path.dirname(src), exist_ok=True)
                shutil.move(dst, src)

    def redo():
        for src, dst in pairs:
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)

    n = len(pairs)
    return Op(f"Move {n} file{'s' if n != 1 else ''}", undo, redo)


def record_copy(transferred: List[Tuple[str, str]]) -> Op:
    """Op for an already-completed copy. Undo removes the copies (to trash)."""
    dsts = [dst for _, dst in transferred]
    trash_map: dict[str, str] = {}

    def undo():
        trash_map.clear()
        for dst in dsts:
            if os.path.exists(dst):
                trash_map[dst] = _move_to_trash(dst)

    def redo():
        for dst, t in list(trash_map.items()):
            _restore(t, dst)
        trash_map.clear()

    n = len(dsts)
    return Op(f"Copy {n} file{'s' if n != 1 else ''}", undo, redo)


def record_rename(old: str, new: str) -> Op:
    def undo(): os.rename(new, old)
    def redo(): os.rename(old, new)
    return Op(f"Rename {os.path.basename(old)}", undo, redo)


def record_new_folder(path: str) -> Op:
    def undo():
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    def redo():
        os.makedirs(path, exist_ok=True)

    return Op(f"New folder {os.path.basename(path)}", undo, redo)
