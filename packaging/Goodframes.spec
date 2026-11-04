# PyInstaller spec for Goodframes.app  -  build with:  .venv/bin/pyinstaller packaging/Goodframes.spec
import os
import cv2

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
CASCADES = os.path.join(cv2.data.haarcascades)

datas = [
    (os.path.join(ROOT, "photocull", "ui"), "photocull/ui"),
    (os.path.join(ROOT, "photocull", "engine", "rubric.md"), "photocull/engine"),
]
for name in ("haarcascade_frontalface_default.xml", "haarcascade_eye_tree_eyeglasses.xml"):
    datas.append((os.path.join(CASCADES, name), "cv2/data"))

a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=[
        "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
        "keyring.backends.macOS", "pillow_heif", "Quartz",
    ],
    excludes=["tkinter", "matplotlib", "pytest", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Goodframes", console=False,
          argv_emulation=False, codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="Goodframes")
app = BUNDLE(
    coll,
    name="Goodframes.app",
    icon=os.path.join(ROOT, "packaging", "Goodframes.icns"),
    bundle_identifier="com.adityaanavekar.goodframes",
    info_plist={
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
