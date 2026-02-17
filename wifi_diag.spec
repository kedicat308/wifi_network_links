# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for WiFi Network Diagnostics Tool.

Usage:
    pyinstaller wifi_diag.spec

This bundles iperf3.exe and cygwin1.dll (must be in the project root)
into a single .exe file.  On the target machine, place a config.ini
next to the .exe to customise default settings.
"""

import os

block_cipher = None
project_root = os.path.dirname(os.path.abspath(SPEC))

# Binary files to bundle alongside the exe (iperf3 + its cygwin dependency)
binaries = []
for fname in ("iperf3.exe", "cygwin1.dll"):
    fpath = os.path.join(project_root, fname)
    if os.path.isfile(fpath):
        # (source, dest_folder_inside_bundle)
        binaries.append((fpath, "."))

a = Analysis(
    ["main.py"],
    pathex=[project_root],
    binaries=binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="wifi_diag",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
