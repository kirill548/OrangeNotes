# Native packaging follow-up · 8 October 2026

Implemented shared D-Bus/Xvfb/Dunst harness in GitHub/GitLab. Explicit opt-in
now fails when the Linux session/tools or macOS bundle identity are missing.
Dunst readiness failures return nonzero; trap cleans its daemon and prints logs.

Existing Flatpak portal adapter retained. Replaced the old YAML manifest with
packaging/flatpak/org.orangenotes.OrangeNotes.json, branch 25.08, local frozen
sources only. flatpak_bundle.py provisions real SDK/Runtime commits into a lock,
checks those commits before offline packaging, disables downloads/updates and
records prebuilt dependency hash. Initial lock must be obtained on Linux, retained
and committed for pinning across clean CI runs; no real commit values are available
on this Windows machine. Preparation requires network; subsequent packaging does
not. Offline installation on a fresh device is a separate requirement.

Added tools/test_mac_bundle.sh: temporary PyInstaller .app, isolated test identity,
ad-hoc signing/verification, execution of the exact native unittest module from
within the bundle, report-file forwarding and nonzero exit on failure or all-skips.
Ad-hoc identity/permission does not validate production signing/notarization.

Deb packaging uses dpkg-deb --root-owner-group; no sudo, installation hooks, system
service payload or root service registration. GitHub Linux job explicitly asserts
non-root uid and verifies the archive via tools/verify_deb.py. Existing AppImage
packaging uses workspace files and the verified tool/runtime; no privileged command.
User service registration remains XDG_CONFIG_HOME/systemd/user + systemctl --user.
No new XDG-autostart fallback is claimed for sessions lacking user systemd.

Validation on Windows:
- test_native_packaging_contracts: 5 PASS, including missing D-Bus hard failure,
  local manifest sources, lock refusal, locked offline builder and deb hook checks.
- test_portal_notifications: 3 PASS.
- test_platform_background: 17 PASS.
- test_release_packaging: 4 PASS.
- Both shell scripts: Git Bash bash -n PASS.
- CI YAML, manifest JSON and Python syntax: PASS.
29 affected tests passed. New lock build test caught/fixed a Path/string expression.
Full application suite was not repeated: changes affect packaging/tests only.
Previous full application result remains 338 total / 336 PASS / 2 SKIP.

Not executed here: Linux daemons, actual dpkg-deb/AppImage/Flatpak builds,
macOS .app/permission flow, CI dispatch. These require their native OS/runners.
Windows executable and user database are unchanged by this follow-up.
