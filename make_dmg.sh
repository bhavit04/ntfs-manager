#!/bin/bash
# Package dist/NTFS Manager.app into a distributable .dmg with an Applications
# shortcut and clear first-launch instructions (needed because the app is unsigned).
set -e
cd "$(dirname "$0")"

APP="dist/NTFS Manager.app"
DMG="dist/NTFS-Manager.dmg"
STAGE="dist/dmg_stage"

[ -d "$APP" ] || { echo "Build the app first: ./build_app.sh"; exit 1; }

echo "==> Staging"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

# First-launch instructions (the app is unsigned, so macOS blocks the first open)
cat > "$STAGE/① READ ME — First Launch.txt" <<'EOF'
NTFS Manager — How to open it the first time
=============================================

NTFS Manager is free and open-source, but it is not signed with a paid
Apple certificate. So the FIRST time you open it, macOS will block it with:

   "NTFS Manager can't be opened because Apple cannot
    check it for malicious software."

This is expected. To allow it (you only do this once):

  1. Drag "NTFS Manager" onto the Applications folder (shown here).
  2. Open it from Applications (double-click). Click "Done" on the warning.
  3. Open  System Settings → Privacy & Security
        (or double-click "② Open Privacy & Security.webloc" in this window)
  4. Scroll to the bottom. You'll see:
        "NTFS Manager was blocked to protect your Mac."
     Click  "Open Anyway".
  5. Confirm, and enter your Mac password.

That's it — from then on it opens normally with a double-click.

After it opens, a Setup Wizard installs the free tools it needs
(FUSE-T + ntfs-3g) and asks for your password once. No reboot needed.
EOF

# One-click shortcut to the Privacy & Security settings pane
cat > "$STAGE/② Open Privacy & Security.webloc" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>URL</key>
	<string>x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension</string>
</dict>
</plist>
EOF

echo "==> Creating DMG"
hdiutil create -volname "NTFS Manager" \
    -srcfolder "$STAGE" -ov -format UDZO "$DMG"

rm -rf "$STAGE"
echo ""
echo "Done.  Distributable: $DMG"
