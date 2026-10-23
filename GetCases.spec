# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['courtlistener_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('data/opinions.jsonl', 'data'), ('person_names.tsv.gz', '.'), ('eng_rep_index.tsv.gz', '.'), ('eng_rep_nominate.tsv.gz', '.'), ('sec_index.tsv.gz', '.'), ('crecb_index.tsv.gz', '.'), ('debates_index.tsv.gz', '.'), ('federal_judges.tsv.gz', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GetCases',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GetCases',
)
app = BUNDLE(
    coll,
    name='GetCases.app',
    icon=None,
    bundle_identifier=None,
)
