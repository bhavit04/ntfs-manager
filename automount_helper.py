#!/usr/bin/env python3
"""
automount_helper.py — run by LaunchAgent on login to auto-mount NTFS drives.
Waits up to 30 s for drives to appear, then mounts any with auto_write=True.
"""

import sys
import os
import time

# Add app directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import list_ntfs_drives, enable_write, notify, MountError
import settings as settings_mod

def main():
    cfg = settings_mod.get()
    if not cfg["automount_enabled"]:
        return

    # Wait for drives to enumerate after login
    time.sleep(8)

    # Retry for up to 30 s (USB hubs can be slow)
    for _ in range(15):
        drives = list_ntfs_drives()
        if drives:
            break
        time.sleep(2)

    mounted = []
    for drive in drives:
        pref = cfg.drive_pref(drive.uuid)
        # Mount if: auto_write flag set, OR automount_enabled + drive is already r/o mounted
        if pref.get("auto_write", True) and drive.status != "write":
            try:
                mp = enable_write(drive)
                mounted.append(f"{drive.name} → {mp}")
            except MountError as e:
                # Log but don't crash
                print(f"[automount] Failed {drive.dev}: {e}", flush=True)

    if mounted and cfg["notify_on_mount"]:
        names = ", ".join(mounted)
        notify("NTFS Manager", f"Auto-mounted: {names}")


if __name__ == "__main__":
    main()
