# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for NTFS Manager. Build with:  pyinstaller "NTFS Manager.spec"

APP_NAME    = "NTFS Manager"
BUNDLE_ID   = "com.ntfsmanager.app"
APP_VERSION = "1.0.0"

a = Analysis(
    ["ntfs_manager.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        "engine", "filebrowser", "transfer", "settings",
        "setup_wizard", "widgets", "automount_helper",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PIL", "numpy", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon="AppIcon.icns",
    bundle_identifier=BUNDLE_ID,
    version=APP_VERSION,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHumanReadableCopyright": "Free & open-source. NTFS read/write for macOS.",
    },
)
