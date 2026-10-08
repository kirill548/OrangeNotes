#!/usr/bin/env bash
# Run on a physical, logged-in macOS desktop; ad-hoc signing is not notarization.
set -euo pipefail
[[ "$(uname -s)" == Darwin ]] || { echo 'This test requires macOS.' >&2; exit 2; }
cd "$(dirname "$0")/.."
python_bin=${PYTHON:-python3}
"$python_bin" -c 'import PyInstaller, Foundation, UserNotifications, objc'
staging=$(mktemp -d "${TMPDIR:-/tmp}/orangenotes-native.XXXXXX")
trap 'rm -rf "$staging"' EXIT
"$python_bin" -m PyInstaller --noconfirm --windowed --onedir --name OrangeNotesNativeTest \
  --osx-bundle-identifier org.orangenotes.notificationtest --codesign-identity - \
  --distpath "$staging/dist" --workpath "$staging/build" --specpath "$staging" \
  --paths "$PWD" --add-data "$PWD/tests/test_native_notification_integration.py:native_tests" \
  --hidden-import app.services.portable_notifications --hidden-import Foundation \
  --hidden-import UserNotifications --hidden-import objc tools/mac_notification_test_entry.py
bundle="$staging/dist/OrangeNotesNativeTest.app"
codesign --force --deep --sign - "$bundle"
codesign --verify --deep --strict "$bundle"
export ORANGE_NATIVE_TEST_REPORT="${ORANGE_NATIVE_TEST_REPORT:-$PWD/mac-native-notifications.log}"
mkdir -p "$(dirname "$ORANGE_NATIVE_TEST_REPORT")"
# Never accept a stale successful report if the new runner fails before writing.
: > "$ORANGE_NATIVE_TEST_REPORT"
echo 'Allow notifications for OrangeNotesNativeTest if macOS asks. No user notes are accessed.'
set +e
"$bundle/Contents/MacOS/OrangeNotesNativeTest"
result=$?
set -e
if [[ -s "$ORANGE_NATIVE_TEST_REPORT" ]]; then cat "$ORANGE_NATIVE_TEST_REPORT"; else echo 'Bundled runner did not produce a report.' >&2; exit 1; fi
exit "$result"
