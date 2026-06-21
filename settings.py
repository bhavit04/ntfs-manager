"""
settings.py — persistent JSON settings store
"""

from __future__ import annotations
import json
import os
from typing import Any

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ntfs_settings.json")

_DEFAULTS: dict[str, Any] = {
    "automount_enabled":   False,   # install LaunchAgent to mount on login
    "notify_on_mount":     True,    # macOS notification when drive is mounted
    "notify_on_complete":  True,    # macOS notification when transfer finishes
    "verify_integrity":    False,   # MD5 checksum verify after copy
    "conflict_action":     "rename",# "rename" | "overwrite" | "skip"
    "prevent_sleep":       True,    # caffeinate during large transfers
    "show_hidden_files":   False,
    "left_pane_path":      "~",
    "right_pane_path":     "/Volumes",
    "known_drives":        {},      # uuid → {"label": ..., "auto_write": bool}
}


class Settings:
    def __init__(self):
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._load()

    def _load(self):
        try:
            if os.path.exists(_PATH):
                with open(_PATH) as f:
                    saved = json.load(f)
                self._data.update(saved)
        except Exception:
            pass

    def save(self):
        try:
            with open(_PATH, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, _DEFAULTS.get(key, default))

    def set(self, key: str, value: Any):
        self._data[key] = value
        self.save()

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any):
        self.set(key, value)

    # Drive-specific prefs
    def drive_pref(self, uuid: str) -> dict:
        return self._data.setdefault("known_drives", {}).get(uuid, {})

    def set_drive_pref(self, uuid: str, key: str, value: Any):
        drives = self._data.setdefault("known_drives", {})
        drives.setdefault(uuid, {})[key] = value
        self.save()


# Module-level singleton
_instance: Settings | None = None

def get() -> Settings:
    global _instance
    if _instance is None:
        _instance = Settings()
    return _instance
