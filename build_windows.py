"""Build the Windows x64 installer from a clean source checkout."""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
APP = ROOT / "electron"
RUNTIME = APP / "node_modules" / "electron" / "dist"
PAYLOAD_MARKER = b"SIERRA-APP-V1\0"


def run(command, **kwargs):
    subprocess.run(command, check=True, **kwargs)


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this build on 64-bit Windows.")
    npm = shutil.which("npm.cmd")
    go = shutil.which("go")
    if npm is None or go is None:
        raise SystemExit("Install Node.js and Go and add both to PATH.")
    profile = json.loads((APP / "profile.json").read_text(encoding="utf-8"))
    personal = profile["personal"]
    stem = "SierraSteward" if personal else "SierraStewardCommunity"
    product = "Sierra Steward" if personal else "Sierra Steward Community"
    BUILD.mkdir(exist_ok=True)
    run([npm, "ci"], cwd=APP)
    run([npm, "exec", "--", "install-electron"], cwd=APP)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
         "--name", "SierraWorker", "--exclude-module", "nacl", "--exclude-module", "davey", "--paths", str(ROOT),
         "--distpath", str(BUILD), "--specpath", str(BUILD), "--workpath", str(BUILD / "worker"),
         "--add-data", f"{ROOT / 'sierra_bot/events/banners'}:sierra_bot/events/banners", str(ROOT / "community_worker.py")])

    payload = BUILD / f"{stem}.app.zip"
    with ZipFile(payload, "w", ZIP_DEFLATED, compresslevel=3) as package:
        for file in sorted(RUNTIME.rglob("*")):
            if file.is_file():
                package.write(file, f"runtime/{file.relative_to(RUNTIME).as_posix()}")
        for name in ("index.html", "main.js", "preload.js", "renderer.js", "style.css", "profile.json", "logo.png"):
            package.write(APP / name, f"dashboard/{name}")
        package.writestr("dashboard/package.json", json.dumps({"name": stem.lower(), "main": "main.js"}))
        for image in sorted((ROOT / "sierra_bot/events/banners").glob("*.png")):
            package.write(image, f"dashboard/{image.name}")
        package.write(BUILD / "SierraWorker.exe", "worker/SierraWorker.exe")
        package.write(ROOT / "THIRD-PARTY-NOTICES.txt", "notices/THIRD-PARTY-NOTICES.txt")
    with payload.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    setup = BUILD / (f"{stem}Setup.exe" if personal else f"{stem}Installer.exe")
    flags = (f"-s -w -H windowsgui -X 'main.product={product}' -X main.appExecutable={stem}.exe "
             f"-X main.payloadName={payload.name} -X main.payloadSHA={digest}")
    run([go, "build", "-trimpath", "-ldflags", flags, "-o", str(setup), "main.go", "wizard.go"],
        cwd=ROOT / "setup", env={**os.environ, "CGO_ENABLED": "0"})
    if not personal:
        with setup.open("ab") as installer, payload.open("rb") as components:
            shutil.copyfileobj(components, installer)
            installer.write(struct.pack("<Q", payload.stat().st_size) + PAYLOAD_MARKER)
        # Verify the public installer without a companion archive in its directory.
        with tempfile.TemporaryDirectory(prefix="sierra-installer-") as scratch:
            independent = shutil.copy2(setup, Path(scratch) / setup.name)
            run([independent, "--verify"])
        print(setup)
        return

    run([setup, "--verify"])
    release = BUILD / f"{stem}-Windows.zip"
    with ZipFile(release, "w", ZIP_STORED) as package:
        for file in (setup, payload, ROOT / "README.md", ROOT / "setup/TERMS.txt", ROOT / "THIRD-PARTY-NOTICES.txt"):
            package.write(file, file.name)
    print(release)


if __name__ == "__main__":
    main()
