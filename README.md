# NTFS Manager

Free, open-source NTFS **read/write** for macOS — including macOS 26 (Tahoe),
where the old kext-based drivers (macFUSE, and the kext builds of Paragon/Tuxera)
no longer work.

It uses **FUSE-T** (kext-free FUSE) + **ntfs-3g** under the hood, and ships its
own dual-pane file browser so you can copy, move, rename, and delete files on
NTFS drives.

---

## Install

1. Open `NTFS-Manager.dmg` and drag **NTFS Manager** to **Applications**.
2. Launch it. On first run a **Setup Wizard** appears and installs everything it
   needs (FUSE-T, ntfs-3g, the macFUSE framework) and configures a one-time
   permission rule. You'll be asked for your Mac password once.
3. That's it — no reboot, no Security & Privacy approval (FUSE-T is kext-free).

**Requirement:** [Homebrew](https://brew.sh) must be installed (the wizard uses it
to fetch the open-source tools). If you don't have it, the wizard tells you.

**No kernel-extension approval needed.** Mounting is done by **FUSE-T**, which is
kext-free — so you do *not* need to enable Reduced Security or approve a system
extension in Startup Security. (The wizard also installs the macFUSE *framework
files* that ntfs-3g links against, and macOS may pop a "System Extension Blocked"
notice when it does — **you can safely ignore that**; this app never loads the
macFUSE kernel extension. The app guarantees ntfs-3g uses FUSE-T's library, not
macFUSE's.)

---

## Using it

1. Plug in your NTFS drive — it appears in the left **NTFS Drives** sidebar.
2. Select it and click **Enable Write**.
3. The **first time** you copy to/from the drive, macOS asks to allow access to
   files on a network volume (FUSE-T mounts appear as one) — click **Allow**.
   This is a normal one-time permission prompt; no Startup Security or kext
   approval is involved.
4. Use the built-in browser: left pane = your Mac, right pane = the NTFS drive,
   middle buttons to **Copy / Move** between them. **New Folder**, rename, and
   delete are available too.
5. Click **Disable Write** (or **Eject**) when you're done.

---

## Important: Finder vs. this app

There's one tradeoff that comes from how kext-free NTFS write works on macOS 26:

| Mode | Driver | Finder shows files? | Writable? |
|------|--------|---------------------|-----------|
| **Disable Write** | Apple's native NTFS (FSKit) | ✅ yes | ❌ no |
| **Enable Write**  | FUSE-T + ntfs-3g            | ❌ no  | ✅ yes |

In **write mode, Finder can't list the files** (FUSE-T presents the volume over
an NFS transport that Finder doesn't enumerate). The files *are* there — **use
this app's browser** to see and manage them while writing. For read-only
browsing, Finder works normally.

This is a limitation of the free kext-free stack on macOS 26, not a bug.

---

## Build from source

```bash
# needs: brew install python@3.13 python-tk@3.13
./build_app.sh      # produces dist/NTFS Manager.app
./make_dmg.sh       # produces dist/NTFS-Manager.dmg
```

Run directly without building:

```bash
python3.13 ntfs_manager.py
```

---

## Distributing to other people

The build is **ad-hoc signed**, which is fine on your own Mac but will trigger
Gatekeeper on someone else's ("can't be opened because Apple cannot check it").

The DMG is built to guide users through this with zero terminal use:
- **`READ ME — First Launch.txt`** — step-by-step instructions in the DMG
- In-app: **Help → ""App can't be opened" — how to allow it"** re-opens the right
  Settings pane any time *after* the first launch. (The app can't auto-handle its
  *own* first block — it isn't running yet — which is why the DMG carries the steps.)

To clear the block (one time, no terminal):

- Open the app once → click **Done** on the warning → **System Settings →
  Privacy & Security** → scroll to the bottom → **"Open Anyway"** → confirm.
- **Proper alternative:** sign and notarize with an Apple Developer ID ($99/yr):

  ```bash
  codesign --deep --force --options runtime \
    --sign "Developer ID Application: YOUR NAME (TEAMID)" "dist/NTFS Manager.app"
  xcrun notarytool submit dist/NTFS-Manager.dmg --apple-id you@example.com \
    --team-id TEAMID --password APP_SPECIFIC_PW --wait
  xcrun stapler staple "dist/NTFS Manager.app"
  ```

---

## Troubleshooting

- **Drive vanished / "busy" on Enable Write** — the app auto-clears stale FUSE-T
  ("zombie") mounts and retries; just try again.
- **Wizard says Homebrew not found** — install it from https://brew.sh, reopen.
- **Password prompt every time** — the permission rule didn't install; re-run the
  wizard (Help → Setup Wizard).

---

## Licenses

This app's own code is MIT-licensed (see `LICENSE`). It installs and drives, but
does not bundle, the following, each under its own license:

- **FUSE-T** — https://github.com/macos-fuse-t/fuse-t
- **ntfs-3g** — GPL — https://github.com/tuxera/ntfs-3g
- **macFUSE** (framework only) — https://github.com/macfuse/macfuse
