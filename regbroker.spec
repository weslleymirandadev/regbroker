# -*- mode: python ; coding: utf-8 -*-
import os
import sys

block_cipher = None

# Get version and OS from environment variables
version = os.environ.get('REGBROKER_VERSION', '1.0.0')
os_name = os.environ.get('REGBROKER_OS', 'unknown')
executable_name = f'regbroker-{version}-{os_name}'

a = Analysis(
    ['src/main.py'],
    pathex=['.'],
    datas=[],
    hiddenimports=[
        'src', 'src.ai', 'src.tui', 'src.bridge',
        'rich', 'prompt_toolkit', 'httpx', 'fpdf2', 'pyperclip',
        'setuptools', 'packaging', 'importlib_metadata'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=executable_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,  # Strip para reduzir tamanho no Linux
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Configurações específicas para Linux ELF
    icon=None,  # Ícones não são suportados em ELF
)
