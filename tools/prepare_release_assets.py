"""Flatten trusted CI package artifacts, rejecting collisions and missing platforms."""
import argparse
import hashlib
import shutil
from pathlib import Path

ARTIFACTS = ("Windows-x64", "Linux-x64", "macOS-Intel", "macOS-AppleSilicon")
EXTENSIONS = (".zip", ".tar.gz", ".dmg", ".AppImage", ".deb", ".flatpak")


def prepare(downloads, destination):
    downloads, destination = Path(downloads), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    assets = {}
    for platform in ARTIFACTS:
        folder = downloads / ("OrangeNotes-" + platform)
        packages = sorted(p for p in folder.rglob("OrangeNotes-*")
                          if p.is_file() and p.name.endswith(EXTENSIONS))
        if not packages:
            raise ValueError("Missing package artifacts: " + platform)
        for package in packages:
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            if package.name in assets and assets[package.name] != digest:
                raise ValueError("Conflicting release filename: " + package.name)
            assets[package.name] = digest
            shutil.copyfile(package, destination / package.name)
    linux = downloads / "OrangeNotes-Linux-x64"
    for suffix in (".deb", ".AppImage", ".flatpak"):
        if not any(p.name.endswith(suffix) for p in linux.rglob("OrangeNotes-*")):
            raise ValueError("Missing Linux release format: " + suffix)
    return assets


def checksums(destination):
    destination = Path(destination)
    files = sorted(p for p in destination.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    text = "".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in files)
    (destination / "SHA256SUMS.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("downloads")
    parser.add_argument("destination")
    args = parser.parse_args()
    prepare(args.downloads, args.destination)
    checksums(args.destination)
