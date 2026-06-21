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
cat > "$STAGE/READ ME — First Launch.txt" <<'EOF'
NTFS Manager — How to open it the first time
=============================================

NTFS Manager is free and open-source, but it is not signed with a paid
Apple certificate. So the FIRST time you open it, macOS will block it with:

   "NTFS Manager can't be opened because Apple cannot
    check it for malicious software."

This is expected and safe. To allow it (you only do this ONCE):

  1. Drag "NTFS Manager" onto the Applications folder (shown here).

  2. Open it from your Applications folder (double-click it).
     macOS shows the warning — click "Done".

  3. Open the Apple menu () -> System Settings -> Privacy & Security.

  4. Scroll ALL THE WAY DOWN. You will see a line that says:
        "NTFS Manager" was blocked to protect your Mac.
     Click the  "Open Anyway"  button next to it.

  5. Confirm "Open Anyway", then enter your Mac password / Touch ID.

That's it — from then on it opens normally with a double-click.

After it opens the first time, a Setup Wizard installs the free tools it
needs (FUSE-T + ntfs-3g) and asks for your password once. No reboot needed.

Tip: inside the app, Help -> '"App can't be opened" — how to allow it'
re-opens the Privacy & Security page for you any time.
EOF

echo "==> Creating DMG"
hdiutil create -volname "NTFS Manager" \
    -srcfolder "$STAGE" -ov -format UDZO "$DMG"

rm -rf "$STAGE"
echo ""
echo "Done.  Distributable: $DMG"
