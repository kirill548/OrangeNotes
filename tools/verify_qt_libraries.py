"""Fail before GUI startup when Linux Qt dependencies are missing."""
import subprocess
import sys
from pathlib import Path


def main():
    if sys.platform != "linux":
        return 0
    from PySide6.QtCore import QLibraryInfo
    plugin = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "platforms/libqxcb.so"
    result = subprocess.run(["ldd", str(plugin)], capture_output=True, text=True, check=False)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode or "not found" in result.stdout:
        print("Missing Qt xcb dependencies: install the libraries listed above.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
