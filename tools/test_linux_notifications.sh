#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in xvfb-run dbus-run-session dunst gdbus notify-send; do
  command -v "$tool" >/dev/null || { echo "Missing native test dependency: $tool" >&2; exit 2; }
done
xvfb-run -a dbus-run-session -- bash -euo pipefail <<'NATIVE_SESSION'
  test -n "${DBUS_SESSION_BUS_ADDRESS:-}" || { echo "D-Bus session was not created" >&2; exit 2; }
  log=$(mktemp)
  dunst >"$log" 2>&1 & daemon=$!
  trap 'kill "$daemon" 2>/dev/null || true; cat "$log"; rm -f "$log"' EXIT
  ready=0
  for attempt in {1..50}; do
    kill -0 "$daemon" 2>/dev/null || { echo "Dunst exited before readiness" >&2; exit 2; }
    if gdbus call --session --dest org.freedesktop.Notifications --object-path /org/freedesktop/Notifications --method org.freedesktop.Notifications.GetCapabilities >/dev/null 2>&1; then ready=1; break; fi
    sleep .1
  done
  test "$ready" = 1 || { echo "D-Bus notification daemon readiness timed out" >&2; exit 2; }
  ORANGE_NATIVE_NOTIFICATION_TESTS=1 "${PYTHON:-python}" -m unittest discover -s tests -p test_native_notification_integration.py
NATIVE_SESSION
