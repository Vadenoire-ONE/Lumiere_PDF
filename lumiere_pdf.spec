# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec для сборки Lumiere PDF в один портативный .exe.

Сборка:
    pyinstaller --clean --noconfirm lumiere_pdf.spec

Результат: dist/LumierePDF.exe — один исполняемый файл, без зависимостей.
"""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = []
hidden += collect_submodules("fitz")  # PyMuPDF
hidden += collect_submodules("PIL")

a = Analysis(
    ["lumiere_pdf.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "numpy",
        "pandas",
        "matplotlib",
        "scipy",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "IPython",
        "jupyter",
        "notebook",
        "tornado",
        "pytest",
    ],
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
    name="LumierePDF",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # пожать UPX, если установлен (не обязательно)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # без чёрного консольного окна
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="lumiere.ico",  # раскомментируйте, если положите свою иконку
)
