# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


spec_root = Path(SPEC).resolve().parent
project_root = spec_root.parent
icon_path = project_root / "packaging" / "assets" / "app.ico"

datas = []
binaries = []
hiddenimports = []

icon_png_path = project_root / "icon.png"
if icon_png_path.exists():
    datas.append((str(icon_png_path), "."))

theme_icons_dir = project_root / "static" / "theme-icons"
if theme_icons_dir.exists():
    for asset_path in sorted(theme_icons_dir.glob("*.png")):
        datas.append((str(asset_path), "static/theme-icons"))

bundled_cpu_ollama_dir = project_root / "vendor" / "ollama-windows-amd64-cpu-0.20.2"
if bundled_cpu_ollama_dir.is_dir():
    for asset_path in sorted(bundled_cpu_ollama_dir.rglob("*")):
        if asset_path.is_file():
            relative_parent = asset_path.relative_to(bundled_cpu_ollama_dir).parent
            destination = Path("vendor") / bundled_cpu_ollama_dir.name
            if str(relative_parent) != ".":
                destination = destination / relative_parent
            datas.append((str(asset_path), str(destination)))

for package_name in ("chromadb",):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package_name)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    [str(project_root / "launcher_desktop.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

desktop_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PrivateNoteDesktop",
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
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    desktop_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PrivateNoteDesktop",
)
