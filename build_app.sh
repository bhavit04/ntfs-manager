#!/bin/bash
# Build NTFS Manager.app into ./dist
# Requires: python3.13 with tkinter (brew install python@3.13 python-tk@3.13)
set -e
cd "$(dirname "$0")"

echo "==> Preparing build environment"
if [ ! -d build_venv ]; then
    python3.13 -m venv build_venv
fi
# shellcheck disable=SC1091
source build_venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet pyinstaller Pillow

echo "==> Generating app icon"
python make_icon.py
rm -rf AppIcon.iconset

echo "==> Building .app bundle"
rm -rf build dist
pyinstaller --noconfirm "NTFS Manager.spec"

echo "==> Ad-hoc code-signing (lets it run locally without Gatekeeper nags)"
codesign --force --deep --sign - "dist/NTFS Manager.app" || true

echo ""
echo "Done.  App is at: dist/NTFS Manager.app"
echo "Drag it to /Applications, or run:  open 'dist/NTFS Manager.app'"
