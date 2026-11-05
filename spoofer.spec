# PyInstaller build config. Build with:  pyinstaller spoofer.spec
# macOS -> dist/LocationSpoofer.app      Windows -> dist/LocationSpoofer/LocationSpoofer.exe
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = [("spoofer/web", "spoofer/web")]
# Several dependencies (e.g. apple_compress) look up their own version via importlib.metadata
# at import time, which fails unless the dist-info metadata is bundled.
datas += copy_metadata("pymobiledevice3", recursive=True)
binaries = []
hiddenimports = []

# pymobiledevice3 imports most of its modules lazily, and its userspace tunnel relies on the
# pmd_* network-stack packages, so PyInstaller's static analysis misses them. Bundle everything.
for pkg in ("pymobiledevice3", "pmd_pytcp", "pmd_net_proto", "pmd_net_addr", "pytun_pmd3",
            "uvicorn", "certifi"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["spoofer/__main__.py"],
    pathex=["."],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["IPython", "tkinter"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocationSpoofer",
    console=True,  # the console window is the "keep this open while spoofing" indicator
)
coll = COLLECT(exe, a.binaries, a.datas, name="LocationSpoofer")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="LocationSpoofer.app",
        bundle_identifier="dev.locationspoofer.app",
        info_plist={"LSBackgroundOnly": False, "NSHighResolutionCapable": True},
    )
