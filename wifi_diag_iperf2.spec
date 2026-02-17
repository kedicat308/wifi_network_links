# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for WiFi Network Diagnostics Tool (iperf2 variant).

Usage:
    pyinstaller wifi_diag_iperf2.spec

This bundles iperf-2.2.1-win64.exe into a single .exe file.
"""

import os

block_cipher = None
project_root = os.path.dirname(os.path.abspath(SPEC))

# Binary files to bundle alongside the exe (iperf2 executable)
binaries = []
for fname in ("iperf-2.2.1-win64.exe", "iperf.exe"):
    fpath = os.path.join(project_root, fname)
    if os.path.isfile(fpath):
        binaries.append((fpath, "."))

a = Analysis(
    ["main_iperf2.py"],
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
    name="wifi_diag_iperf2",
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
