"""
engine.py — disk detection, NTFS mount/unmount, volume ops, LaunchAgent, notifications
"""

from __future__ import annotations
import os
import re
import shlex
import shutil
import subprocess
import plistlib
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

NTFS3G_CANDIDATES = [
    "/opt/homebrew/sbin/ntfs-3g",
    "/usr/local/sbin/ntfs-3g",
    "/opt/homebrew/bin/ntfs-3g",
    "/usr/local/bin/ntfs-3g",
]
NTFSFIX_CANDIDATES = [
    "/opt/homebrew/sbin/ntfsfix",
    "/usr/local/sbin/ntfsfix",
    "/opt/homebrew/bin/ntfsfix",
    "/usr/local/bin/ntfsfix",
]
MKNTFS_CANDIDATES = [
    "/opt/homebrew/sbin/mkntfs",
    "/usr/local/sbin/mkntfs",
    "/opt/homebrew/bin/mkntfs",
    "/usr/local/bin/mkntfs",
]
MACFUSE_MARKERS = [
    "/Library/Filesystems/macfuse.fs",
    "/Library/Extensions/macfuse.kext",
    "/usr/local/lib/libfuse.dylib",
    "/opt/homebrew/lib/libfuse.dylib",
]
# FUSE-T (kext-free FUSE for macOS 26) ships its lib under /usr/local/lib
FUSET_MARKERS = [
    "/usr/local/lib/libfuse-t.dylib",
    "/usr/local/lib/libfuse.2.dylib",
]
LIBFUSE_TARGET  = "/usr/local/lib/libfuse-t.dylib"
LIBFUSE_SYMLINK = "/usr/local/lib/libfuse.2.dylib"
SUDOERS_FILE    = "/etc/sudoers.d/ntfs-manager"

# Homebrew packages for the working macOS 26 chain (label, brew-install args)
BREW_STEPS = [
    ("FUSE-T",            "macos-fuse-t/homebrew-cask/fuse-t"),
    ("ntfs-3g",           "gromgit/fuse/ntfs-3g-mac"),
    ("macFUSE framework", "--cask macfuse"),
]
NTFS_CONTENT_TYPES = {"Windows_NTFS", "Microsoft Basic Data"}
_NTFS_EXCLUDE     = {"Microsoft Reserved", "EFI System Partition",
                     "Windows Recovery Environment"}

LAUNCH_AGENT_LABEL = "com.ntfsmanager.automount"
LAUNCH_AGENT_PLIST = os.path.expanduser(
    f"~/Library/LaunchAgents/{LAUNCH_AGENT_LABEL}.plist"
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DiskUsage:
    total: int
    used:  int
    free:  int

    @property
    def percent_used(self) -> float:
        return (self.used / self.total * 100) if self.total else 0.0

    def fmt(self, b: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"

    @property
    def used_str(self)  -> str: return self.fmt(self.used)
    @property
    def free_str(self)  -> str: return self.fmt(self.free)
    @property
    def total_str(self) -> str: return self.fmt(self.total)


@dataclass
class Partition:
    dev:        str
    name:       str
    fs:         str
    mount:      str
    size_bytes: int
    ntfs_write: bool
    uuid:       str = ""

    @property
    def size_str(self) -> str:
        b = self.size_bytes
        for u in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024: return f"{b:.1f} {u}"
            b /= 1024
        return f"{b:.1f} PB"

    @property
    def is_ntfs(self) -> bool:
        if self.fs in _NTFS_EXCLUDE:
            return False
        if self.size_bytes < 32 * 1024 * 1024:
            return False
        fs = self.fs.lower()
        return "ntfs" in fs or self.fs in NTFS_CONTENT_TYPES or "microsoft" in fs

    @property
    def status(self) -> str:
        if not self.mount:   return "unmounted"
        if self.ntfs_write:  return "write"
        return "readonly"

    def disk_usage(self) -> Optional[DiskUsage]:
        if not self.mount:
            return None
        try:
            u = shutil.disk_usage(self.mount)
            return DiskUsage(total=u.total, used=u.used, free=u.free)
        except OSError:
            return None


# ---------------------------------------------------------------------------
# Low-level shell helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str]) -> Tuple[str, str, int]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def _run_admin(shell_cmd: str) -> Tuple[str, str, int]:
    """Run shell_cmd with macOS admin-password prompt (osascript)."""
    escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
    script  = f'do shell script "{escaped}" with administrator privileges'
    return _run(["osascript", "-e", script])


def _q(path: str) -> str:
    """Shell-quote a path safely."""
    return shlex.quote(path)


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------

def _find_tool(candidates: list[str], name: str) -> Optional[str]:
    for p in candidates:
        if os.path.exists(p):
            return p
    out, _, rc = _run(["which", name])
    return out if rc == 0 else None


def find_ntfs3g()  -> Optional[str]: return _find_tool(NTFS3G_CANDIDATES,  "ntfs-3g")
def find_ntfsfix() -> Optional[str]: return _find_tool(NTFSFIX_CANDIDATES, "ntfsfix")
def find_mkntfs()  -> Optional[str]: return _find_tool(MKNTFS_CANDIDATES,  "mkntfs")

def macfuse_installed() -> bool:
    return any(os.path.exists(m) for m in MACFUSE_MARKERS)

def fuset_installed() -> bool:
    return any(os.path.exists(m) for m in FUSET_MARKERS)

def fuse_t_linked() -> bool:
    """True if /usr/local/lib/libfuse.2.dylib resolves to FUSE-T's lib (kext-free).
    If it resolves to macFUSE's own libfuse, ntfs-3g would use the macFUSE KEXT,
    which requires the user to approve it in Startup Security. We avoid that."""
    try:
        real = os.path.realpath(LIBFUSE_SYMLINK)
    except OSError:
        return False
    return "libfuse-t" in os.path.basename(real)

def brew_installed() -> bool:
    # Check known locations first — a Finder-launched .app has a minimal PATH
    # that usually doesn't include /opt/homebrew/bin.
    if os.path.exists("/opt/homebrew/bin/brew") or os.path.exists("/usr/local/bin/brew"):
        return True
    _, _, rc = _run(["which", "brew"])
    return rc == 0

def sudoers_active() -> bool:
    """True if the passwordless sudo rule for ntfs-3g is installed and working."""
    fix = find_ntfsfix()
    if not fix:
        return False
    _, _, rc = _run(["sudo", "-n", fix, "--version"])
    return rc == 0


def brew_path() -> Optional[str]:
    for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if os.path.exists(p):
            return p
    return None


def open_privacy_security() -> None:
    """Open System Settings → Privacy & Security (where the "Open Anyway" button
    for a blocked app and removable-volume permissions live)."""
    for url in (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension",
        "x-apple.systempreferences:com.apple.preference.security",
    ):
        _, _, rc = _run(["open", url])
        if rc == 0:
            return
    _run(["open", "/System/Library/PreferencePanes/Security.prefPane"])


def askpass_path() -> str:
    """Create (once) a GUI askpass helper so Homebrew's internal `sudo` can prompt
    for a password from a windowed app (no terminal). Returns its path."""
    d = os.path.expanduser("~/Library/Application Support/NTFS Manager")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "askpass.sh")
    script = (
        "#!/bin/bash\n"
        "osascript -e 'text returned of (display dialog "
        '"NTFS Manager needs administrator access to install the FUSE drivers." '
        'with title "NTFS Manager Setup" default answer "" with hidden answer '
        "giving up after 120)'\n"
    )
    with open(p, "w") as f:
        f.write(script)
    os.chmod(p, 0o755)
    return p


def configure_privileges() -> Tuple[bool, str]:
    """Install the scoped passwordless-sudo rule + libfuse symlink via ONE admin
    prompt. The rule lets the app run ntfs-3g/ntfsfix/umount/diskutil/mkdir without
    a password popup each time. Validated with `visudo -c` before installing.
    Returns (ok, message)."""
    out, _, _ = _run(["id", "-un"])
    user = out or os.environ.get("USER", "")
    if not user:
        return False, "Could not determine current user."
    ntfs3g  = find_ntfs3g()  or "/opt/homebrew/bin/ntfs-3g"
    ntfsfix = find_ntfsfix() or "/opt/homebrew/bin/ntfsfix"
    rule = (
        f"{user} ALL=(ALL) NOPASSWD: {ntfs3g}, {ntfsfix}, "
        "/opt/homebrew/Cellar/ntfs-3g-mac/*/bin/ntfs-3g, "
        "/opt/homebrew/Cellar/ntfs-3g-mac/*/bin/ntfsfix, "
        "/usr/local/Cellar/ntfs-3g-mac/*/bin/ntfs-3g, "
        "/usr/local/Cellar/ntfs-3g-mac/*/bin/ntfsfix, "
        "/sbin/umount, /usr/sbin/diskutil, /bin/mkdir -p /Volumes/*"
    )
    tmp = "/tmp/ntfs-manager.sudoers"
    script = " && ".join([
        f"printf '%s\\n' {_q(rule)} > {tmp}",
        f"visudo -cf {tmp}",
        f"install -m 0440 -o root -g wheel {tmp} {SUDOERS_FILE}",
        f"rm -f {tmp}",
        "mkdir -p /usr/local/lib",
        f"(test -e {LIBFUSE_TARGET} && ln -sf {LIBFUSE_TARGET} {LIBFUSE_SYMLINK} || true)",
    ])
    o, e, rc = _run_admin(script)
    if rc != 0:
        return False, (e or o or "Privilege setup failed.")
    return True, "Permissions configured."


class DepStatus:
    def __init__(self):
        self.ntfs3g  = find_ntfs3g()
        self.ntfsfix = find_ntfsfix()
        self.mkntfs  = find_mkntfs()
        self.macfuse = macfuse_installed()
        self.fuset   = fuset_installed()
        self.linked  = fuse_t_linked()
        self.brew    = brew_installed()
        self.sudoers = sudoers_active()

    @property
    def fuse_ok(self) -> bool:
        # Need the macFUSE framework (MFMount) AND FUSE-T AND ntfs-3g pointed at
        # FUSE-T's lib (so the macFUSE KEXT is never required).
        return self.macfuse and self.fuset and self.linked

    @property
    def ready(self) -> bool:
        return bool(self.ntfs3g) and self.fuse_ok and self.sudoers

    def missing(self) -> list[str]:
        out = []
        if not self.fuset:   out.append("FUSE-T")
        if not self.ntfs3g:  out.append("ntfs-3g")
        if not self.macfuse: out.append("macFUSE framework")
        if not self.linked:  out.append("FUSE-T link")
        if not self.sudoers: out.append("permissions")
        return out


# ---------------------------------------------------------------------------
# Disk scanning
# ---------------------------------------------------------------------------

def _parse_partitions(entry: dict, parent_dev: str) -> list[Partition]:
    results = []
    dev_id = entry.get("DeviceIdentifier", parent_dev)
    fs     = entry.get("Content", "") or entry.get("FilesystemType", "")
    name   = entry.get("VolumeName", "") or dev_id
    mount  = entry.get("MountPoint", "")
    size   = entry.get("Size", 0)
    uuid   = entry.get("VolumeUUID", "") or entry.get("DiskUUID", "")

    partitions = entry.get("Partitions", [])
    if partitions:
        for p in partitions:
            results.extend(_parse_partitions(p, dev_id))
    else:
        results.append(Partition(
            dev=f"/dev/{dev_id}", name=name, fs=fs,
            mount=mount, size_bytes=size, ntfs_write=False, uuid=uuid,
        ))
    return results


def _ntfs3g_active_mounts() -> dict:
    """Map device -> mountpoint for drives currently write-mounted by ntfs-3g.

    Reads the live ntfs-3g processes (e.g. `ntfs-3g /dev/disk4s3 /Volumes/OS -o …`).
    This is authoritative and, crucially, works for FUSE-T (NFS) mounts where
    `diskutil` reports the raw device as unmounted.
    """
    out, _, _ = _run(["ps", "-axww", "-o", "command="])
    result = {}
    for line in out.splitlines():
        if "ntfs-3g" not in line or "/dev/" not in line:
            continue
        m = re.search(r"ntfs-3g\s+(/dev/\S+)\s+(/.+?)\s+-o(?:\s|$)", line)
        if m:
            result[m.group(1)] = m.group(2)
    return result


def list_ntfs_drives() -> list[Partition]:
    out, _, rc = _run(["diskutil", "list", "-plist", "external"])
    if rc != 0:
        return []
    try:
        data = plistlib.loads(out.encode())
    except Exception:
        return []

    active = _ntfs3g_active_mounts()   # dev -> mountpoint (write mounts, incl. FUSE-T)
    results: list[Partition] = []
    for disk in data.get("AllDisksAndPartitions", []):
        for p in _parse_partitions(disk, disk.get("DeviceIdentifier", "")):
            if p.is_ntfs:
                if p.dev in active:
                    p.mount = active[p.dev]   # FUSE-T NFS mount diskutil can't see
                    p.ntfs_write = True
                results.append(p)
    return results


def get_partition_info(dev: str) -> dict:
    """Return full diskutil info dict for a device."""
    out, _, rc = _run(["diskutil", "info", "-plist", dev])
    if rc != 0:
        return {}
    try:
        return plistlib.loads(out.encode())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Mount / unmount
# ---------------------------------------------------------------------------

class MountError(Exception):
    pass


def _clear_stale_mount(mp: str, dev: str) -> None:
    """Force-clear a zombie/ghost FUSE-T mount left behind by a disrupted unmount.

    Symptom: `ls -ld mp` works but `ls mp` fails, and a fresh ntfs-3g mount reports
    'Resource busy' / 'already exclusively opened' even though no process holds it.
    Force-unmounting the dead mountpoint releases the kernel's stale vnode so the
    device can be remounted. Safe to call even when nothing is mounted.
    """
    # Try every unmount avenue; all are best-effort.
    _run(["diskutil", "unmount", "force", mp])
    _run(["sudo", "umount", "-f", mp])
    _run(["sudo", "umount", "-f", dev])


def _ensure_mountpoint(mp: str) -> None:
    """Create the /Volumes/<name> mountpoint. /Volumes is root:wheel so a normal
    user cannot mkdir there — use sudo (NOPASSWD rule covers /bin/mkdir)."""
    import os as _os
    if _os.path.isdir(mp):
        return
    out, err, rc = _run(["sudo", "/bin/mkdir", "-p", mp])
    if rc != 0:
        raise MountError(f"Could not create mount point {mp}: {err or out}")


def enable_write(part: Partition) -> str:
    """Mount NTFS partition with write access via ntfs-3g. Returns mount point."""
    ntfs3g = find_ntfs3g()
    if not ntfs3g:
        raise MountError("ntfs-3g not found. Open Setup Wizard.")
    if not macfuse_installed():
        raise MountError("macFUSE not found. Open Setup Wizard.")

    vol_name = re.sub(r"[^\w\-. ]", "_", part.name) or "NTFS"
    mp = f"/Volumes/{vol_name}"

    # Step 1: unmount existing macOS read-only mount + force-clear any ghost mount,
    # so the device is free and the mountpoint is in a known state before we start.
    if part.mount:
        _run(["diskutil", "unmount", part.mount])
    _clear_stale_mount(mp, part.dev)

    # Step 2: create mount point (needs root — /Volumes is root:wheel)
    _ensure_mountpoint(mp)

    # Step 3: clear dirty bit via sudo (NOPASSWD rule in /etc/sudoers.d/ntfs-manager)
    ntfsfix_path = find_ntfsfix()
    if ntfsfix_path:
        _run(["sudo", ntfsfix_path, part.dev])

    # Step 4: mount via sudo ntfs-3g (NOPASSWD rule — no password prompt, no TCC sandbox).
    # Retry once after force-clearing a stale mount if the device comes back "busy".
    import os as _os
    uid = _os.getuid()
    gid = _os.getgid()
    mount_cmd = [
        "sudo", ntfs3g, part.dev, mp,
        "-o",
        f"local,allow_other,big_writes,noatime,remove_hiberfile"
        f",uid={uid},gid={gid},umask=022,volname={vol_name}",
    ]
    out, err, rc = _run(mount_cmd)
    if rc != 0 and re.search(r"busy|already.*opened|already mounted", (err + out), re.I):
        _clear_stale_mount(mp, part.dev)
        _ensure_mountpoint(mp)
        if ntfsfix_path:
            _run(["sudo", ntfsfix_path, part.dev])
        out, err, rc = _run(mount_cmd)
    if rc != 0:
        raise MountError(f"Mount failed: {err or out}")
    return mp


def disable_write(part: Partition) -> None:
    """Unmount ntfs-3g mount; macOS remounts read-only."""
    if not part.mount:
        raise MountError("Drive is not mounted.")
    # sudo umount via NOPASSWD rule — no popup needed
    out, err, rc = _run(["sudo", "umount", part.mount])
    if rc != 0:
        # fallback to diskutil
        out, err, rc = _run(["diskutil", "unmount", part.mount])
    if rc != 0:
        raise MountError(f"Unmount failed: {err or out}")
    # Force-clear any stale FUSE-T vnode so a later Enable Write isn't blocked by "busy"
    _clear_stale_mount(part.mount, part.dev)
    # Remount read-only via Apple's native NTFS driver (best-effort, no admin popup)
    _run(["diskutil", "mount", part.dev])


def eject(part: Partition) -> None:
    _run_admin(f"diskutil eject {_q(part.dev)}")


# ---------------------------------------------------------------------------
# Volume operations
# ---------------------------------------------------------------------------

def check_volume(part: Partition) -> Tuple[bool, str]:
    """
    Run ntfsfix to check and repair NTFS inconsistencies.
    The volume must be unmounted first.
    Returns (success, output_text).
    """
    ntfsfix = find_ntfsfix()
    if not ntfsfix:
        return False, "ntfsfix not found. Is ntfs-3g installed?"

    # Unmount if needed
    if part.mount:
        _run_admin(f"diskutil unmount {_q(part.mount)} || umount {_q(part.mount)} || true")

    out, err, rc = _run_admin(f"{_q(ntfsfix)} {_q(part.dev)}")
    output = (out + "\n" + err).strip()

    # Re-mount read-only after check
    _run_admin(f"diskutil mount {_q(part.dev)}")

    return rc == 0, output or ("Volume check completed." if rc == 0 else "ntfsfix failed.")


def format_volume(dev: str, label: str) -> Tuple[bool, str]:
    """
    Format device as NTFS with the given label.
    Device must be unmounted. THIS IS DESTRUCTIVE.
    """
    mkntfs = find_mkntfs()
    if not mkntfs:
        return False, "mkntfs not found. Is ntfs-3g installed?"

    safe_label = re.sub(r"[\"'\\]", "", label)[:32]
    cmd = f"{_q(mkntfs)} -f -L {_q(safe_label)} {_q(dev)}"
    out, err, rc = _run_admin(cmd)
    output = (out + "\n" + err).strip()
    return rc == 0, output or ("Format complete." if rc == 0 else "mkntfs failed.")


def format_exfat(dev: str, label: str) -> Tuple[bool, str]:
    """
    Format a partition as exFAT using macOS diskutil. THIS IS DESTRUCTIVE.
    No extra tools required — exFAT is natively supported by macOS.
    diskutil handles unmounting internally.
    """
    safe_label = re.sub(r"[\"'\\]", "", label)[:32]
    cmd = f"diskutil eraseVolume ExFAT {_q(safe_label)} {_q(dev)}"
    out, err, rc = _run_admin(cmd)
    output = (out + "\n" + err).strip()
    return rc == 0, output or ("Format complete." if rc == 0 else "diskutil failed.")


def rename_volume(part: Partition, new_label: str) -> Tuple[bool, str]:
    """Change the NTFS volume label using ntfslabel if available, else diskutil."""
    # Try diskutil (works for some NTFS variants)
    safe = re.sub(r"[\"'\\]", "", new_label)[:32]
    out, err, rc = _run_admin(
        f"diskutil rename {_q(part.dev)} {_q(safe)}"
    )
    if rc == 0:
        return True, f"Renamed to '{safe}'."
    return False, err or out


# ---------------------------------------------------------------------------
# LaunchAgent (automount at login)
# ---------------------------------------------------------------------------

def _app_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def launch_agent_installed() -> bool:
    return os.path.exists(LAUNCH_AGENT_PLIST)


def install_launch_agent() -> Tuple[bool, str]:
    helper = os.path.join(_app_dir(), "automount_helper.py")
    python3 = shutil.which("python3") or "/usr/bin/python3"
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>{LAUNCH_AGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python3}</string>
    <string>{helper}</string>
  </array>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <false/>
  <key>StandardOutPath</key>  <string>/tmp/ntfsmanager-mount.log</string>
  <key>StandardErrorPath</key><string>/tmp/ntfsmanager-mount.log</string>
</dict>
</plist>
"""
    try:
        os.makedirs(os.path.dirname(LAUNCH_AGENT_PLIST), exist_ok=True)
        with open(LAUNCH_AGENT_PLIST, "w") as f:
            f.write(plist_content)
        _run(["launchctl", "unload", LAUNCH_AGENT_PLIST])
        _, err, rc = _run(["launchctl", "load", LAUNCH_AGENT_PLIST])
        if rc != 0:
            return False, err
        return True, "Auto-mount at login enabled."
    except Exception as e:
        return False, str(e)


def uninstall_launch_agent() -> Tuple[bool, str]:
    try:
        _run(["launchctl", "unload", LAUNCH_AGENT_PLIST])
        if os.path.exists(LAUNCH_AGENT_PLIST):
            os.remove(LAUNCH_AGENT_PLIST)
        return True, "Auto-mount at login disabled."
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# macOS notifications
# ---------------------------------------------------------------------------

def notify(title: str, message: str, sound: bool = True) -> None:
    sound_part = ' sound name "Glass"' if sound else ""
    script = (
        f'display notification {_q(message)} '
        f'with title {_q(title)}{sound_part}'
    )
    subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# Drive watcher (polls every 2 s on a background thread)
# ---------------------------------------------------------------------------

class DriveWatcher:
    def __init__(self, callback: Callable[[list[Partition]], None], interval: float = 2.0):
        self._cb       = callback
        self._interval = interval
        self._last: list[Partition] = []
        self._stop     = threading.Event()
        self._thread   = threading.Thread(target=self._loop, daemon=True)

    def start(self): self._thread.start()
    def stop(self):  self._stop.set()

    def _loop(self):
        while not self._stop.wait(self._interval):
            current = list_ntfs_drives()
            if not self._same(current, self._last):
                self._last = current
                self._cb(current)

    @staticmethod
    def _same(a: list[Partition], b: list[Partition]) -> bool:
        if len(a) != len(b):
            return False
        return all(
            x.dev == y.dev and x.mount == y.mount and x.ntfs_write == y.ntfs_write
            for x, y in zip(a, b)
        )
