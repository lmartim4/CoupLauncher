"""
Build script for CoupLauncher.

Creates a standalone executable using PyInstaller and packages it for distribution.
Mirrors the build system used by the Coup game repository.

Usage:
    python build_launcher.py

Environment variables:
    RELEASE_VERSION  Version string injected by GitHub Actions (e.g. "v0.1.0").
                     Defaults to "v0.0.0-dev" when run locally.
"""

import os
import sys
import shutil
import zipfile
import tarfile
import subprocess
from pathlib import Path

APP_NAME     = "CoupLauncher"
ENTRY_POINT  = "launcher.py"
BUILD_OUTPUT = Path("build_output")
PLATFORM     = sys.platform           # "win32", "linux", "darwin"
VERSION      = os.environ.get("RELEASE_VERSION", "v0.0.0-dev")


def build() -> None:
    print(f"Building {APP_NAME} {VERSION} for {PLATFORM}...")

    # Clean previous intermediate artifacts
    for d in ("dist", "build", "__pycache__"):
        if Path(d).exists():
            shutil.rmtree(d)
    BUILD_OUTPUT.mkdir(exist_ok=True)

    pyinstaller_cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",       # no console popup; creates .app bundle on macOS
        "--name", APP_NAME,
        "--clean",
        "--noconfirm",
        ENTRY_POINT,
    ]

    subprocess.run(pyinstaller_cmd, check=True)
    _package()
    print("Build complete.")


def _package() -> None:
    """Wrap the PyInstaller output in a platform archive inside build_output/."""
    if PLATFORM == "win32":
        exe = Path("dist") / f"{APP_NAME}.exe"
        out = BUILD_OUTPUT / f"{APP_NAME}-Windows-{VERSION}.zip"
        print(f"Packaging {out.name}...")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(exe, exe.name)

    elif PLATFORM == "darwin":
        # PyInstaller --windowed creates a .app bundle directory on macOS
        app_bundle = Path("dist") / f"{APP_NAME}.app"
        out = BUILD_OUTPUT / f"{APP_NAME}-macOS-{VERSION}.tar.gz"
        print(f"Packaging {out.name}...")
        with tarfile.open(out, "w:gz") as tf:
            tf.add(app_bundle, arcname=app_bundle.name)

    else:  # Linux
        exe = Path("dist") / APP_NAME
        out = BUILD_OUTPUT / f"{APP_NAME}-Linux-{VERSION}.tar.gz"
        print(f"Packaging {out.name}...")
        with tarfile.open(out, "w:gz") as tf:
            tf.add(exe, arcname=exe.name)

    print(f"Archive ready: {out}")


if __name__ == "__main__":
    build()
