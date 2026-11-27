import hashlib
import plistlib
import re
import shutil
import sys
import zipfile
from pathlib import Path


def filehash(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify_manifest(folder, name, expected):
    [manifest] = list(folder.rglob(name))
    rows = {}
    for line in manifest.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
        if not match or match[2] in rows:
            raise ValueError(f"Invalid checksum entry in {name}")
        rows[match[2]] = match[1]
    if set(rows) != set(expected):
        raise ValueError(f"Unexpected files in {name}: {list(rows)}")
    for file in expected:
        if filehash(manifest.parent / file) != rows[file]:
            raise ValueError(f"Invalid checksum: {file}")


source, upload = map(Path, sys.argv[1:3])
version, ios_build = sys.argv[3:]
windows = source / "OrangeNotes-Windows-x64"
ios = source / "OrangeNotes-iOS-unsigned-requires-resigning"
verify_manifest(windows, "SHA256SUMS-Windows.txt", ("Orange Notes Setup.exe", "OrangeNotes-Windows-AMD64.zip"))
verify_manifest(ios, "SHA256SUMS.txt", ("OrangeNotes-iOS-unsigned.ipa",))
assets = [
    (windows, "Orange Notes Setup.exe", "Orange-Notes-Setup.exe"),
    (windows, "OrangeNotes-Windows-AMD64.zip", None),
    (source / "OrangeNotes-Linux-x64", "OrangeNotes-*.tar.gz", None),
    (source / "OrangeNotes-Linux-x64", "OrangeNotes-*.AppImage", None),
    (source / "OrangeNotes-Linux-x64", "OrangeNotes-*.deb", None),
    (source / "OrangeNotes-Linux-x64", "OrangeNotes-*.flatpak", None),
    (source / "OrangeNotes-macOS-Intel", "OrangeNotes-*.zip", None),
    (source / "OrangeNotes-macOS-Intel", "OrangeNotes-*.dmg", None),
    (source / "OrangeNotes-macOS-AppleSilicon", "OrangeNotes-*.zip", None),
    (source / "OrangeNotes-macOS-AppleSilicon", "OrangeNotes-*.dmg", None),
    (source / "OrangeNotes-Android-debug", "app-debug.apk", "OrangeNotes-Android-debug.apk"),
    (ios, "OrangeNotes-iOS-unsigned.ipa", None),
]
upload.mkdir(parents=True, exist_ok=True)
checksums = {}
for folder, pattern, alias in assets:
    matches = list(folder.rglob(pattern))
    if len(matches) != 1 or not matches[0].is_file() or not matches[0].stat().st_size:
        raise ValueError(f"Expected exactly one nonempty {pattern} in {folder}: {matches}")
    path = matches[0]
    name = alias or path.name
    if name in checksums:
        raise ValueError(f"Duplicate release filename: {name}")
    if path.suffix in {".zip", ".apk", ".ipa"}:
        with zipfile.ZipFile(path) as archive:
            if damaged := archive.testzip():
                raise ValueError(f"Damaged ZIP entry: {name}: {damaged}")
            if path.suffix == ".ipa":
                meta = plistlib.loads(archive.read("Payload/Runner.app/Info.plist"))
                if (meta.get("CFBundleShortVersionString"), meta.get("CFBundleVersion")) != (version, ios_build):
                    raise ValueError("Wrong iOS version")
            if folder.name.startswith("OrangeNotes-macOS"):
                [plist] = [item for item in archive.namelist() if item.endswith("OrangeNotes.app/Contents/Info.plist")]
                meta = plistlib.loads(archive.read(plist))
                if meta.get("CFBundleShortVersionString") != version:
                    raise ValueError(f"Wrong macOS version: {name}")
    shutil.copyfile(path, upload / name)
    checksums[name] = filehash(upload / name)
    print(f"{name} ({path.stat().st_size} bytes)")
(upload / "SHA256SUMS.txt").write_text(
    "".join(f"{checksums[name]}  {name}\n" for name in sorted(checksums)),
    encoding="ascii", newline="\n"
)
