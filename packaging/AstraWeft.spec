# -*- mode: python ; coding: utf-8 -*-
"""Cross-platform, one-folder PyInstaller definition for AstraWeft."""

from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


ROOT = Path(SPECPATH).resolve().parent
SOURCE_ROOTS = (
    ROOT / "src",
    ROOT / "packages" / "provider-sdk" / "src",
    ROOT / "plugins" / "custom-rest" / "src",
    ROOT / "plugins" / "mock" / "src",
    ROOT / "plugins" / "openai" / "src",
    ROOT / "plugins" / "runway" / "src",
)

DISTRIBUTIONS = (
    "astraweft",
    "astraweft-custom-rest-provider",
    "astraweft-provider-sdk",
    "astraweft-mock-provider",
    "astraweft-openai-provider",
    "astraweft-runway-provider",
)
PLUGIN_PACKAGES = (
    "astraweft_custom_rest_provider",
    "astraweft_mock_provider",
    "astraweft_openai_provider",
    "astraweft_runway_provider",
)

codesign_identity = os.environ.get("ASTRAWEFT_CODESIGN_IDENTITY") or None
entitlements_file = os.environ.get("ASTRAWEFT_MACOS_ENTITLEMENTS") or None
if sys.platform != "darwin" and (codesign_identity or entitlements_file):
    raise RuntimeError("macOS signing inputs are only valid on macOS")
if entitlements_file is not None:
    entitlement_path = Path(entitlements_file).expanduser().resolve()
    if not entitlement_path.is_file():
        raise FileNotFoundError(f"macOS entitlements file not found: {entitlement_path}")
    entitlements_file = str(entitlement_path)

datas = [
    (str(ROOT / "LICENSE"), "legal"),
    (str(ROOT / "NOTICE"), "legal"),
    (
        str(ROOT / "src" / "astraweft" / "infrastructure" / "database" / "alembic.ini"),
        "astraweft/infrastructure/database",
    ),
    (
        str(ROOT / "src" / "astraweft" / "infrastructure" / "database" / "migrations"),
        "astraweft/infrastructure/database/migrations",
    ),
]
for distribution in DISTRIBUTIONS:
    datas += copy_metadata(distribution)
for package in PLUGIN_PACKAGES:
    datas += collect_data_files(package)
datas += collect_data_files("rfc3987_syntax")

hiddenimports = collect_submodules("keyring.backends")
hiddenimports += ["aiosqlite", "sqlalchemy.dialects.sqlite.aiosqlite"]
for package in PLUGIN_PACKAGES:
    hiddenimports += collect_submodules(package)

analysis = Analysis(
    [str(ROOT / "src" / "astraweft" / "__main__.py")],
    pathex=[str(path) for path in SOURCE_ROOTS],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "mypy", "ruff"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="AstraWeft",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=codesign_identity,
    entitlements_file=entitlements_file,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AstraWeft",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="AstraWeft.app",
        bundle_identifier="dev.astraweft.app",
        version="0.1.0",
        info_plist={
            "CFBundleDisplayName": "AstraWeft",
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
        },
    )
