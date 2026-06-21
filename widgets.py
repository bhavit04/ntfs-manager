"""
widgets.py — custom Tk widgets.

macOS Tk ignores `bg` on tk.Button (it always paints the native light-gray
button face), which makes coloured buttons impossible and white-on-gray text
unreadable. FlatButton is built from a Frame + Label — both honour `bg` — so
buttons render in their real colour with a hover effect and proper contrast.
"""

from __future__ import annotations
import tkinter as tk
from typing import Callable, Optional


def _shade(hex_color: str, factor: float) -> str:
    """Lighten (factor>1) or darken (factor<1) a #rrggbb colour."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    clamp = lambda x: max(0, min(255, int(x * factor)))
    return f"#{clamp(r):02x}{clamp(g):02x}{clamp(b):02x}"


class FlatButton(tk.Frame):
    """A flat, colour-honouring button. Supports .config(state=/text=/bg=/fg=/command=)
    so it is a drop-in for the tk.Button calls used in this app."""

    def __init__(
        self, parent, text: str = "", command: Optional[Callable] = None,
        bg: str = "#7c6af7", fg: str = "#ffffff",
        font=("Helvetica Neue", 11, "bold"),
        padx: int = 12, pady: int = 8, wraplength: int = 0,
        state: str = "normal",
        disabled_bg: str = "#2f2f44", disabled_fg: str = "#6c7086",
        **kw,
    ):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, **kw)
        self._bg          = bg
        self._fg          = fg
        self._hover_bg    = _shade(bg, 1.18)
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg
        self._command     = command
        self._state       = "normal"

        self._label = tk.Label(
            self, text=text, bg=bg, fg=fg, font=font,
            justify="center", cursor="hand2",
            wraplength=wraplength if wraplength else 0,
        )
        self._label.pack(fill="both", expand=True, padx=padx, pady=pady)

        for w in (self, self._label):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>",    self._enter)
            w.bind("<Leave>",    self._leave)

        if state == "disabled":
            self.config(state="disabled")

    # -- internal painting -------------------------------------------------

    def _paint(self, bg: str, fg: Optional[str] = None) -> None:
        tk.Frame.configure(self, bg=bg)
        self._label.configure(bg=bg)
        if fg is not None:
            self._label.configure(fg=fg)

    def _click(self, _=None):
        if self._state == "normal" and self._command:
            self._command()

    def _enter(self, _=None):
        if self._state == "normal":
            self._paint(self._hover_bg)

    def _leave(self, _=None):
        if self._state == "normal":
            self._paint(self._bg)

    # -- tk.Button-compatible config --------------------------------------

    def config(self, **kw):  # type: ignore[override]
        if "command" in kw:
            self._command = kw.pop("command")
        if "text" in kw:
            self._label.configure(text=kw.pop("text"))

        # Track explicit colours given in THIS call so a disabled button can
        # still be styled (e.g. a green "Done ✓" state).
        explicit_bg = kw.pop("bg", None)
        explicit_fg = kw.pop("fg", None)
        if explicit_bg is not None:
            self._bg = explicit_bg
            self._hover_bg = _shade(explicit_bg, 1.18)
        if explicit_fg is not None:
            self._fg = explicit_fg
        if "state" in kw:
            self._state = kw.pop("state")

        # Repaint to reflect current state + colours.
        if self._state == "disabled":
            self._paint(explicit_bg or self._disabled_bg,
                        explicit_fg or self._disabled_fg)
            self._label.configure(cursor="arrow")
        else:
            self._paint(self._bg, self._fg)
            self._label.configure(cursor="hand2")

        if kw:
            tk.Frame.configure(self, **kw)

    configure = config
