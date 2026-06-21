"""
filebrowser.py — dual-pane file browser with context menu, full file ops,
                 keyboard shortcuts, search, breadcrumb path bar.
"""

from __future__ import annotations
import os
import shutil
import stat
import subprocess
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from typing import Callable, Optional

from widgets import FlatButton

_PLACEHOLDER = "__ph__"

C = {
    "bg":      "#1e1e2e",
    "surface": "#2a2a3e",
    "surface2":"#313145",
    "accent":  "#7c6af7",
    "success": "#4ade80",
    "danger":  "#f87171",
    "text":    "#cdd6f4",
    "subtext": "#a6adc8",
    "border":  "#363653",
    "sel_bg":  "#45475a",
}

_EXT_ICONS = {
    "mp4":"🎬","mkv":"🎬","avi":"🎬","mov":"🎬","wmv":"🎬","m4v":"🎬","webm":"🎬",
    "mp3":"🎵","flac":"🎵","aac":"🎵","wav":"🎵","m4a":"🎵","ogg":"🎵",
    "jpg":"🖼","jpeg":"🖼","png":"🖼","gif":"🖼","bmp":"🖼","heic":"🖼","webp":"🖼","raw":"🖼",
    "pdf":"📕","doc":"📝","docx":"📝","txt":"📄","md":"📄","xlsx":"📊","xls":"📊","csv":"📊",
    "zip":"🗜","tar":"🗜","gz":"🗜","rar":"🗜","7z":"🗜",
    "py":"🐍","js":"📜","ts":"📜","html":"🌐","css":"🎨","sh":"⚙","json":"⚙",
    "dmg":"💿","iso":"💿","img":"💿",
}

def _icon(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _EXT_ICONS.get(ext, "📄")

def _fmt_size(b: int) -> str:
    if b < 0: return ""
    for u in ("B","KB","MB","GB","TB"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"

def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

def _is_hidden(name: str) -> bool:
    return name.startswith(".")


# ---------------------------------------------------------------------------
# Single pane
# ---------------------------------------------------------------------------

class BrowserPane(tk.Frame):
    def __init__(
        self, parent, title: str = "Files",
        root_path: str = os.path.expanduser("~"),
        show_hidden: bool = False,
        writable: bool = True,
        confined: bool = False,
        on_selection_change: Optional[Callable[[list[str]], None]] = None,
        on_navigate: Optional[Callable[[str], None]] = None,
        **kwargs,
    ):
        super().__init__(parent, bg=C["bg"], **kwargs)
        self.title       = title
        self._root_path  = os.path.realpath(root_path)
        self._cwd        = self._root_path
        self._show_hidden = show_hidden
        self.writable    = writable          # True = allow destructive ops
        self._confined   = confined          # True = cannot navigate above root_path
        self._on_sel     = on_selection_change or (lambda _: None)
        self._on_nav     = on_navigate or (lambda _: None)
        self._search_var = tk.StringVar()
        self._build()
        self.navigate(root_path)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        # Header
        hdr = tk.Frame(self, bg=C["surface"], pady=6, padx=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text=self.title, font=("Helvetica Neue", 11, "bold"),
                 bg=C["surface"], fg=C["accent"]).pack(side="left")

        # Path / breadcrumb bar
        path_bar = tk.Frame(self, bg=C["surface2"], pady=5, padx=8)
        path_bar.pack(fill="x")

        self._up_btn = FlatButton(
            path_bar, text="↑", font=("Helvetica Neue", 13, "bold"),
            bg=C["border"], fg=C["text"], padx=9, pady=2,
            command=self._go_up,
        )
        self._up_btn.pack(side="left")

        self._path_var = tk.StringVar()
        # Inset rounded-ish field (a darker box) for a more finished look
        path_box = tk.Frame(path_bar, bg=C["bg"])
        path_box.pack(side="left", fill="x", expand=True, padx=6)
        self._path_entry = tk.Entry(
            path_box, textvariable=self._path_var,
            font=("Menlo", 10),
            bg=C["bg"], fg=C["subtext"],
            insertbackground=C["text"], relief="flat", bd=0,
            highlightthickness=0,
        )
        self._path_entry.pack(fill="x", expand=True, padx=8, pady=4)
        self._path_entry.bind("<Return>", lambda _: self.navigate(self._path_var.get()))

        self._hidden_var = tk.BooleanVar(value=self._show_hidden)
        tk.Checkbutton(
            path_bar, text="Hidden", variable=self._hidden_var,
            font=("Helvetica Neue", 9), bg=C["surface2"], fg=C["subtext"],
            selectcolor=C["surface2"], activebackground=C["surface2"],
            relief="flat", bd=0, cursor="hand2",
            command=self._toggle_hidden,
        ).pack(side="right")

        # Search bar (hidden by default)
        self._search_frame = tk.Frame(self, bg=C["surface2"], padx=8, pady=3)
        tk.Label(self._search_frame, text="🔍", bg=C["surface2"],
                 fg=C["subtext"]).pack(side="left")
        tk.Entry(
            self._search_frame, textvariable=self._search_var,
            font=("Helvetica Neue", 10),
            bg=C["surface2"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0,
        ).pack(side="left", fill="x", expand=True, padx=4)
        self._search_var.trace_add("write", lambda *_: self._refresh())
        tk.Button(
            self._search_frame, text="✕", font=("Helvetica Neue", 9),
            bg=C["surface2"], fg=C["subtext"],
            relief="flat", bd=0, cursor="hand2",
            command=self.close_search,
        ).pack(side="right")

        # Treeview
        tree_frame = tk.Frame(self, bg=C["bg"], highlightthickness=0, bd=0)
        tree_frame.pack(fill="both", expand=True)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("FB.Treeview",
                        background=C["bg"], foreground=C["text"],
                        fieldbackground=C["bg"], rowheight=26,
                        font=("Helvetica Neue", 11),
                        borderwidth=0, relief="flat")
        style.layout("FB.Treeview", [           # strip the default field border
            ("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("FB.Treeview.Heading",
                        background=C["surface"], foreground=C["subtext"],
                        font=("Helvetica Neue", 10, "bold"),
                        relief="flat", borderwidth=0, padding=(8, 5))
        style.map("FB.Treeview.Heading",
                  background=[("active", C["surface2"])])
        style.map("FB.Treeview",
                  background=[("selected", C["accent"])],
                  foreground=[("selected", "#ffffff")])

        # Dark, slim scrollbars that match the theme (no chunky white bars)
        for orient in ("Vertical", "Horizontal"):
            style.configure(f"FB.{orient}.TScrollbar",
                            background=C["border"], troughcolor=C["bg"],
                            bordercolor=C["bg"], arrowcolor=C["subtext"],
                            relief="flat", borderwidth=0)
            style.map(f"FB.{orient}.TScrollbar",
                      background=[("active", C["accent"])])

        self._tree = ttk.Treeview(tree_frame, style="FB.Treeview",
                                  columns=("size", "modified"),
                                  selectmode="extended")
        self._tree.heading("#0",       text="Name",     anchor="w")
        self._tree.heading("size",     text="Size",     anchor="e")
        self._tree.heading("modified", text="Modified", anchor="w")
        self._tree.column("#0",       stretch=True, minwidth=160)
        self._tree.column("size",     width=80,  anchor="e", stretch=False)
        self._tree.column("modified", width=130, anchor="w", stretch=False)

        # Subtle alternating row stripes (professional list look)
        self._tree.tag_configure("oddrow",  background=C["bg"])
        self._tree.tag_configure("evenrow", background=C["surface"])

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self._tree.yview, style="FB.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",
                            command=self._tree.xview, style="FB.Horizontal.TScrollbar")
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        # Empty-state placeholder (shown when the list has no items)
        self._placeholder = tk.Label(
            tree_frame, text="", font=("Helvetica Neue", 12),
            bg=C["bg"], fg=C["subtext"], justify="center")

        # Bindings
        self._tree.bind("<<TreeviewOpen>>",   self._on_open)
        self._tree.bind("<Double-1>",          self._on_double_click)
        self._tree.bind("<<TreeviewSelect>>",  self._on_select)
        self._tree.bind("<Button-2>",          self._show_context_menu)
        self._tree.bind("<Control-Button-1>",  self._show_context_menu)
        self._tree.bind("<Delete>",            self._delete_selected)
        self._tree.bind("<BackSpace>",         lambda _: self._go_up())
        self._tree.bind("<Return>",            self._open_selected)
        self._tree.bind("<Command-a>",         self._select_all)
        self._tree.bind("<Command-f>",         lambda _: self.open_search())
        self._tree.bind("<Command-r>",         lambda _: self._refresh())
        self._tree.bind("<Escape>",            lambda _: self.close_search())
        self._tree.bind("<F2>",                lambda _: self._rename_selected())

        # Status bar
        self._status = tk.Label(self, text="", font=("Helvetica Neue", 9),
                                bg=C["surface"], fg=C["subtext"],
                                anchor="w", padx=8, pady=4)
        self._status.pack(fill="x")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(self, path: str):
        path = os.path.realpath(os.path.expanduser(path))
        if not os.path.isdir(path): return
        # In confined mode (NTFS pane), never go above the drive root — keeps
        # write ops like New Folder from targeting root-owned /Volumes.
        if self._confined and not (path == self._root_path
                                   or path.startswith(self._root_path + os.sep)):
            path = self._root_path
        self._cwd = path
        self._path_var.set(path)
        self._refresh()
        self._on_nav(path)

    def set_root(self, path: str):
        self._root_path = os.path.realpath(path)
        self.navigate(self._root_path)

    def _go_up(self):
        if self._confined and self._cwd == self._root_path:
            return                          # already at drive root
        parent = os.path.dirname(self._cwd)
        if parent != self._cwd:
            self.navigate(parent)

    def _toggle_hidden(self):
        self._show_hidden = self._hidden_var.get()
        self._refresh()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def open_search(self):
        self._search_frame.pack(fill="x", after=self._path_entry.master)
        self._search_var.set("")

    def close_search(self):
        self._search_var.set("")
        self._search_frame.pack_forget()
        self._refresh()

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _refresh(self):
        self._tree.delete(*self._tree.get_children())
        self._placeholder.place_forget()
        query = self._search_var.get().lower()
        if query:
            self._populate_search("", self._cwd, query)
        else:
            self._populate("", self._cwd)

    def _populate(self, parent_iid: str, dirpath: str):
        try:
            raw = list(os.scandir(dirpath))
        except (PermissionError, FileNotFoundError, OSError):
            return

        def _sort_key(e):
            try:
                is_d = e.is_dir(follow_symlinks=False)
            except OSError:
                is_d = False
            return (not is_d, e.name.lower())

        try:
            entries = sorted(raw, key=_sort_key)
        except OSError:
            entries = raw

        count = 0
        for entry in entries:
            if not self._show_hidden and _is_hidden(entry.name):
                continue
            count += 1
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                st     = entry.stat(follow_symlinks=False)
                size   = "" if is_dir else _fmt_size(st.st_size)
                mtime  = _fmt_time(st.st_mtime)
                icon   = "📁" if is_dir else _icon(entry.name)
            except OSError:
                is_dir, size, mtime, icon = False, "", "", "📄"

            stripe = "evenrow" if count % 2 == 0 else "oddrow"
            iid = self._tree.insert(
                parent_iid, "end",
                text=f"  {icon}  {entry.name}",
                values=(size, mtime),
                tags=(entry.path, stripe),
                open=False,
            )
            if is_dir:
                self._tree.insert(iid, "end", iid=f"{iid}{_PLACEHOLDER}", text="")

        if parent_iid == "":
            self._status.config(text=f"  {count} items")
            self._update_placeholder(count)

    def _update_placeholder(self, count: int):
        """Show a friendly centered message when the list is empty."""
        if count == 0:
            if self.writable:
                msg = "This folder is empty.\nDrag files here or use “Copy to Drive”."
            elif self.title == "NTFS Drive":
                msg = ("Select a drive on the left, then click “Enable Write”\n"
                       "to browse and edit its files here.")
            else:
                msg = "This folder is empty."
            self._placeholder.config(text=msg)
            self._placeholder.place(relx=0.5, rely=0.45, anchor="center")
        else:
            self._placeholder.place_forget()

    def _populate_search(self, parent_iid: str, dirpath: str, query: str):
        try:
            for entry in sorted(os.scandir(dirpath),
                                key=lambda e: e.name.lower()):
                if not self._show_hidden and _is_hidden(entry.name):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    if is_dir:
                        self._populate_search(parent_iid, entry.path, query)
                    if query in entry.name.lower():
                        st   = entry.stat(follow_symlinks=False)
                        size = "" if is_dir else _fmt_size(st.st_size)
                        icon = "📁" if is_dir else _icon(entry.name)
                        self._tree.insert(
                            parent_iid, "end",
                            text=f"  {icon}  {entry.name}",
                            values=(size, _fmt_time(st.st_mtime)),
                            tags=(entry.path,),
                        )
                except OSError:
                    pass
        except (PermissionError, OSError):
            pass

    def _on_open(self, _event=None):
        iid = self._tree.focus()
        children = self._tree.get_children(iid)
        if len(children) == 1 and children[0].endswith(_PLACEHOLDER):
            self._tree.delete(children[0])
            path = self._iid_path(iid)
            if path: self._populate(iid, path)

    def _on_double_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid: return
        path = self._iid_path(iid)
        if path and os.path.isdir(path):
            self.navigate(path)

    def _open_selected(self, _event=None):
        iids = self._tree.selection()
        if len(iids) == 1:
            path = self._iid_path(iids[0])
            if path and os.path.isdir(path):
                self.navigate(path)
                return
        # Open in Finder
        for iid in iids:
            path = self._iid_path(iid)
            if path: subprocess.Popen(["open", path])

    def _on_select(self, _event=None):
        self._on_sel(self.selected_paths())

    def _select_all(self, _event=None):
        self._tree.selection_set(self._tree.get_children())

    def _iid_path(self, iid: str) -> Optional[str]:
        tags = self._tree.item(iid, "tags")
        return tags[0] if tags else None

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _delete_selected(self, _event=None):
        paths = self.selected_paths()
        if not paths: return
        if not self.writable:
            messagebox.showwarning("Read-only",
                                   "Enable write access first.", parent=self)
            return
        names = "\n".join(os.path.basename(p) for p in paths[:5])
        extra = f"\n…and {len(paths)-5} more" if len(paths) > 5 else ""
        if not messagebox.askyesno(
            "Delete", f"Permanently delete?\n{names}{extra}", parent=self
        ): return

        for p in paths:
            try:
                if os.path.isdir(p): shutil.rmtree(p)
                else:                os.remove(p)
            except Exception as e:
                messagebox.showerror("Delete Error",
                                     f"{os.path.basename(p)}: {e}", parent=self)
        self._refresh()

    def _rename_selected(self, _event=None):
        paths = self.selected_paths()
        if len(paths) != 1: return
        if not self.writable:
            messagebox.showwarning("Read-only",
                                   "Enable write access first.", parent=self)
            return
        old_path = paths[0]
        old_name = os.path.basename(old_path)
        new_name = simpledialog.askstring(
            "Rename", f"Rename '{old_name}' to:", initialvalue=old_name, parent=self
        )
        if not new_name or new_name == old_name: return
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        try:
            os.rename(old_path, new_path)
        except Exception as e:
            messagebox.showerror("Rename Error", str(e), parent=self)
        self._refresh()

    def new_folder(self):
        if not self.writable:
            messagebox.showwarning("Read-only",
                                   "Enable write access first.", parent=self)
            return
        name = simpledialog.askstring("New Folder", "Folder name:", parent=self)
        if not name: return
        path = os.path.join(self._cwd, name)
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)
        self._refresh()

    def get_info(self, path: str):
        try:
            st = os.stat(path)
            is_dir = os.path.isdir(path)
            if is_dir:
                count = sum(len(files) for _, _, files in os.walk(path))
                size_info = f"{count} files"
            else:
                size_info = _fmt_size(st.st_size)
            info = (
                f"Name:     {os.path.basename(path)}\n"
                f"Path:     {path}\n"
                f"Type:     {'Folder' if is_dir else 'File'}\n"
                f"Size:     {size_info}\n"
                f"Modified: {_fmt_time(st.st_mtime)}\n"
                f"Created:  {_fmt_time(st.st_ctime)}\n"
                f"Mode:     {stat.filemode(st.st_mode)}"
            )
        except Exception as e:
            info = str(e)
        messagebox.showinfo(f"Info — {os.path.basename(path)}", info, parent=self)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, event):
        iid = self._tree.identify_row(event.y)
        if iid:
            if iid not in self._tree.selection():
                self._tree.selection_set(iid)
        paths = self.selected_paths()

        menu = tk.Menu(self, tearoff=False,
                       bg=C["surface"], fg=C["text"],
                       activebackground=C["accent"],
                       activeforeground="white",
                       relief="flat", bd=1)

        if paths:
            single = len(paths) == 1
            menu.add_command(label="Open in Finder",
                             command=lambda: [subprocess.Popen(["open", p]) for p in paths])
            if single:
                menu.add_command(label="Get Info",
                                 command=lambda: self.get_info(paths[0]))
            menu.add_separator()
            if self.writable:
                if single:
                    menu.add_command(label="Rename    (F2)",
                                     command=self._rename_selected)
                menu.add_command(
                    label="Delete    (⌫)",
                    command=self._delete_selected,
                    foreground=C["danger"],
                )
            else:
                menu.add_command(label="Delete (enable write first)",
                                 state="disabled")
            menu.add_separator()

        if self.writable:
            menu.add_command(label="New Folder", command=self.new_folder)
        menu.add_command(label="Refresh  (⌘R)", command=self._refresh)
        menu.add_command(label="Show Hidden Files",
                         command=self._toggle_hidden)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def selected_paths(self) -> list[str]:
        return [self._iid_path(iid)
                for iid in self._tree.selection()
                if self._iid_path(iid)]

    @property
    def cwd(self) -> str:
        return self._cwd

    def refresh(self):
        self._refresh()


# ---------------------------------------------------------------------------
# Dual pane
# ---------------------------------------------------------------------------

class DualBrowser(tk.Frame):
    def __init__(
        self, parent,
        on_copy: Optional[Callable[[list[str], str], None]] = None,
        on_move: Optional[Callable[[list[str], str], None]] = None,
        **kwargs,
    ):
        super().__init__(parent, bg=C["bg"], **kwargs)
        self._on_copy = on_copy or (lambda s, d: None)
        self._on_move = on_move or (lambda s, d: None)
        self._build()

    def _build(self):
        paned = tk.PanedWindow(self, orient="horizontal",
                               bg=C["border"], sashwidth=4, sashrelief="flat")
        paned.pack(fill="both", expand=True)

        self.left = BrowserPane(paned, title="Mac Storage",
                                root_path=os.path.expanduser("~"),
                                writable=True)
        paned.add(self.left, minsize=260)

        mid = tk.Frame(paned, bg=C["surface"], width=96)
        paned.add(mid, minsize=80)
        self._build_mid(mid)

        self.right = BrowserPane(paned, title="NTFS Drive",
                                 root_path="/Volumes", writable=False,
                                 confined=True)
        paned.add(self.right, minsize=260)

    def _build_mid(self, parent):
        parent.pack_propagate(False)
        inner = tk.Frame(parent, bg=C["surface"])
        inner.place(relx=0.5, rely=0.5, anchor="center")

        def _btn(text, cmd, color=C["accent"]):
            FlatButton(inner, text=text, font=("Helvetica Neue", 11, "bold"),
                       bg=color, fg="white", padx=6, pady=8, wraplength=80,
                       command=cmd).pack(fill="x", pady=4)

        _btn("→ Copy\nto Drive",  self._copy_right)
        _btn("← Copy\nto Mac",   self._copy_left)
        _btn("→ Move\nto Drive",  self._move_right, "#5a4fcf")
        _btn("← Move\nto Mac",   self._move_left,  "#5a4fcf")

        # New Folder on right pane
        FlatButton(inner, text="📁 New\nFolder", font=("Helvetica Neue", 10, "bold"),
                   bg=C["border"], fg=C["text"], padx=6, pady=7, wraplength=80,
                   command=lambda: self.right.new_folder()).pack(fill="x", pady=(12, 0))

    def _copy_right(self):
        s, d = self.left.selected_paths(), self.right.cwd
        if s and d: self._on_copy(s, d)

    def _copy_left(self):
        s, d = self.right.selected_paths(), self.left.cwd
        if s and d: self._on_copy(s, d)

    def _move_right(self):
        s, d = self.left.selected_paths(), self.right.cwd
        if s and d: self._on_move(s, d)

    def _move_left(self):
        s, d = self.right.selected_paths(), self.left.cwd
        if s and d: self._on_move(s, d)

    def set_ntfs_root(self, path: str, writable: bool = False):
        self.right.writable = writable
        self.right.set_root(path)

    def set_ntfs_writable(self, writable: bool):
        self.right.writable = writable

    def refresh_right(self): self.right.refresh()
    def refresh_left(self):  self.left.refresh()
