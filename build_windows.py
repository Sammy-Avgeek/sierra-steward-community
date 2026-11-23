"""Build the Windows x64 Community Edition from a clean source checkout."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
APP = ROOT / "electron"
RUNTIME = APP / "node_modules" / "electron" / "dist"
STEM = "SierraStewardCommunity"


def run(command, **kwargs):
    subprocess.run(command, check=True, **kwargs)


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this build on 64-bit Windows.")
    npm = shutil.which("npm.cmd")
    go = shutil.which("go")
    if npm is None or go is None:
        raise SystemExit("Install Node.js and Go and add both to PATH.")
    BUILD.mkdir(exist_ok=True)
    run([npm, "ci"], cwd=APP)
    run([npm, "exec", "--", "install-electron"], cwd=APP)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
         "--name", "SierraWorker", "--exclude-module", "nacl", "--exclude-module", "davey", "--paths", str(ROOT),
         "--distpath", str(BUILD), "--specpath", str(BUILD), "--workpath", str(BUILD / "worker"),
         "--add-data", f"{ROOT / 'sierra_bot/events/banners'}:sierra_bot/events/banners", str(ROOT / "community_worker.py")])

    payload = BUILD / f"{STEM}.app.zip"
    with ZipFile(payload, "w", ZIP_DEFLATED, compresslevel=3) as package:
        for file in RUNTIME.rglob("*"):
            if file.is_file():
                name = file.relative_to(RUNTIME).as_posix()
                package.write(file, f"{STEM}.exe" if name == "electron.exe" else name)
        for name in ("index.html", "main.js", "preload.js", "renderer.js", "style.css", "profile.json", "logo.png"):
            package.write(APP / name, f"resources/app/{name}")
        package.writestr("resources/app/package.json", json.dumps({"name": "sierra-steward-community", "main": "main.js"}))
        for image in (ROOT / "sierra_bot/events/banners").glob("*.png"):
            package.write(image, f"resources/app/{image.name}")
        package.write(BUILD / "SierraWorker.exe", "resources/SierraWorker.exe")
    with payload.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    setup = BUILD / f"{STEM}Setup.exe"
    flags = f"-s -w -H windowsgui -X main.payloadSHA={digest}"
    run([go, "build", "-trimpath", "-ldflags", flags, "-o", str(setup), "main.go", "wizard.go"],
        cwd=ROOT / "setup", env={**os.environ, "CGO_ENABLED": "0"})
    run([setup, "--verify"])

    release = BUILD / f"{STEM}-Windows.zip"
    with ZipFile(release, "w", ZIP_STORED) as package:
        for file in (setup, payload, ROOT / "README.md", ROOT / "setup/TERMS.txt", ROOT / "THIRD-PARTY-NOTICES.txt"):
            package.write(file, file.name)
    print(release)


if __name__ == "__main__":
    main()
