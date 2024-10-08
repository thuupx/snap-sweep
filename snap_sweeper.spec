# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

libsvm_datas, libsvm_binaries, libsvm_hiddenimports = collect_all("libsvm")
chromadb_datas, chromadb_binaries, chromadb_hiddenimports = collect_all("chromadb")
brisque_datas, brisque_binaries, brisque_hiddenimports = collect_all("brisque")
ctk_data = collect_data_files("customtkinter")

snap_sweeper_path = os.path.join(os.path.abspath(os.curdir), "snap_sweeper")


a = Analysis(
    ["snap_sweeper/__main__.py"],
    pathex=[os.path.abspath(os.curdir)],
    binaries=[
        *libsvm_binaries,
        *chromadb_binaries,
        *brisque_binaries,
    ],
    datas=[
        ("snap_sweeper/resources", "resources/"),
        *ctk_data,
        *libsvm_datas,
        *chromadb_datas,
        *brisque_datas,
        (snap_sweeper_path, "snap_sweeper"),
    ],
    hiddenimports=[
        *libsvm_hiddenimports,
        *chromadb_hiddenimports,
        *brisque_hiddenimports,
        "snap_sweeper",
        "snap_sweeper.app_manager",
        "snap_sweeper.snap_sweeper_app",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["set_env_vars.py"],  # Add the runtime hook here
    excludes=[],
    noarchive=False,
    optimize=0,
    module_collection_mode={
        "chromadb": "py",
    },
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Snap Sweeper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="snap_sweeper/resources/icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Snap Sweeper",
)
app = BUNDLE(
    coll,
    name="Snap Sweeper.app",
    bundle_identifier=None,
    icon="snap_sweeper/resources/icon.icns",
    info_plist={
        "CFBundleName": "Snap Sweeper",
        "CFBundleDisplayName": "Snap Sweeper",
        "CFBundleExecutable": "Snap Sweeper",
    },
)
