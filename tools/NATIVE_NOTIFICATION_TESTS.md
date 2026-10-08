# Native verification and packaging

The full regression suite has transport mocks. Real daemon tests are separate:

```sh
ORANGE_NATIVE_NOTIFICATION_TESTS=1 python -m unittest discover -s tests -p test_native_notification_integration.py
```

Linux requires a D-Bus user session, `notify-send`, `gdbus` and an actionable
notification daemon. CI starts Dunst inside a disposable D-Bus/Xvfb session,
checks GetCapabilities, submits a notification, verifies its numeric ID, then
closes it. This validates real request acceptance/removal, not banner visibility
or a human clicking Done/Snooze. D-Bus failures, missing session address and missing tools fail the opted-in test.
Both CI systems invoke `bash tools/test_linux_notifications.sh`; the harness
checks daemon readiness, retains failure output and always stops its own Dunst.
Executable harness regression tests inject a missing D-Bus session and a crashed
Dunst process; both cases must return exit code 2. GitLab runs package creation
and verification as the unprivileged `orange-packager` user after root-only
container dependency provisioning; no package installation hooks are executed.

macOS requires execution inside a bundled application with a Bundle Identifier.
Plain Python fails when native testing is explicitly enabled. The manual `native_macos_notifications`
GitHub workflow input runs the packaged `--notification-self-test` on a
self-hosted runner labelled `orangenotes-notifications`. Use a logged-in desktop,
a stable Developer ID identity, and grant Orange Notes notification permission
before running. Both manual CI jobs additionally run `tools/test_mac_bundle.sh`
and retain `mac-native-notifications.log` even on failure. The ad-hoc test identity
has its own permission prompt and requires an operator on the logged-in desktop.
Denied permission, transport rejection, or missing bundle identity
fails the probe; no user database is accessed. GitLab has an equivalent protected,
manual macOS job. Human banner visibility and button clicks remain device checks.

Windows diagnostic evidence is ToastNotifier.Setting. DisabledForUser means
user-wide denial, but does not prove which switch or policy caused it. Logs record
only identity and setting changes; never note text or action tokens. Enabled does
not prove visibility: Focus/Do Not Disturb and desktop policies can suppress banners.

The native matrix produces separate Windows x64, Linux x64, macOS Intel and Apple
Silicon packages. GitHub accepts either existing certificate/provider settings
(`WINDOWS_SIGN_THUMBPRINT`, `SIGNTOOL_PATH`, `MAC_SIGN_IDENTITY`,
`MAC_NOTARY_PROFILE`) or ephemeral CI credentials:

- Windows: `WINDOWS_CERTIFICATE_BASE64` (PFX), `WINDOWS_CERTIFICATE_PASSWORD`.
- macOS: `MAC_CERTIFICATE_BASE64` (P12), `MAC_CERTIFICATE_PASSWORD`,
  `MAC_SIGN_IDENTITY`. Notarization additionally needs `APPLE_ID`, `APPLE_TEAM_ID`,
  `APPLE_APP_PASSWORD` together.

Store all credentials in CI secrets. Certificate import is disabled for pull
requests. Temporary certificates/keychains are removed with an `always()` step;
Windows preserves a certificate already present before import. Self-hosted runners
should be disposable if the job is force-killed before cleanup. Supplied invalid
credentials fail the build; absent credentials produce explicitly unsigned builds.
No certificates are shipped in artifacts. GitLab macOS uses a pre-provisioned
Developer ID/keychain profile on its protected shell runner.

GitHub builds AppImage with a checksum-verified tool/runtime and deb with gdbus.
The optional `flatpak` manual input builds a sandbox bundle using
`packaging/flatpak/org.orangenotes.OrangeNotes.json` (SDK/Runtime branch 25.08).
The first Linux preparation resolves **actual immutable OSTree commits**, writing
`packaging/flatpak/runtime-lock.json`. Retain/commit this file for reproducible
subsequent builds; without a committed lock, each fresh CI checkout bootstraps a
new lock and therefore is not pinned across runs. A supplied lock is reused, not
silently updated. The initial lock is now present, resolved from the two real HTTPS Flathub branch
refs on Ubuntu WSL. See runtime-lock.provenance.json. Installing these revisions
and verifying the signed OSTree objects with Flatpak remains a Linux-runner check.

```sh
# Online provisioning; install the exact locked revisions if a lock already exists.
python tools/flatpak_bundle.py --prepare --bootstrap
# Subsequent provisioning uses --prepare without --bootstrap and retains the lock.
# Offline packaging: requires installed matching SDK/Runtime and frozen Linux bundle.
python tools/flatpak_bundle.py
```

The second command rejects missing/mismatched locks and uses local sources only,
`--disable-download` and `--disable-updates`. No pip/network operation occurs in
build commands. The frozen dependency bundle is hashed in `Flatpak-provenance.json`;
CI uploads it together with the actual lock and bundle. Offline packaging does not
mean runtimes are included for offline installation on another device.

Flatpak is experimental until tested on a Linux desktop. It has no host filesystem,
network or full session-bus permission. Notifications use the desktop portal;
Done/Snooze are available inside the attention list. Host systemd installation is
disabled. A worker runs while the app remains open (including tray); full Exit stops
it, and overdue events recover on reopening. Persistent sandbox background delivery
and external local-model connections are not claimed. Runtime commits are checked against the lock before packaging.

Primary references:
- https://learn.microsoft.com/en-us/uwp/api/windows.ui.notifications.toastnotifier.setting
- https://specifications.freedesktop.org/notification/latest-single/
- https://developer.apple.com/documentation/usernotifications/asking-permission-to-use-notifications
- https://docs.flatpak.org/en/latest/sandbox-permissions.html

## Physical macOS validation

```sh
python3 -m pip install -r requirements-build.txt
bash tools/test_mac_bundle.sh
```

The helper creates an isolated temporary .app with identity
`org.orangenotes.notificationtest`, collects the native integration test and
PyObjC frameworks, applies/verifies ad-hoc signing, and executes unittest **from
inside that bundle**. Test output is written to a file because windowed bundles
may have no stdout; the shell prints the report and propagates failure. Temporary
build files are cleaned on exit. Allow notification permission for the test app;
if the first prompt times out, grant permission and rerun. The test requires exactly
one executed macOS case and one Linux skip. Permission for this test identity does
not prove the production Developer ID identity is authorised. Ad-hoc signing is
only for local validation, not trusted distribution or notarization.

## Unprivileged Debian/AppImage validation

GitHub's Linux runner checks `id -u != 0`, builds the deb, then invokes
`python tools/verify_deb.py dist/OrangeNotes-Linux-x64.deb`. The verifier checks
root:root **archive metadata**, required launchers, absence of system service files
and absence of installation/removal hooks. `--root-owner-group` does not need root.
The package is not installed by these tools. AppImage packing likewise operates in
`dist/OrangeNotes.AppDir` without sudo; installation of host test libraries in CI
is a separate provisioning step.

At first normal launch, the app registers `~/.config/systemd/user/orangenotes-reminders.service`
(or XDG_CONFIG_HOME equivalent) and uses `systemctl --user`. There is no root systemd
service or package postinst. Flatpak intentionally bypasses this host registration.
Linux sessions without user systemd now use an owned XDG Autostart desktop file
under XDG_CONFIG_HOME/autostart (or ~/.config/autostart). Missing manager/bus causes
fallback; permission failures do not. Startup is user-level and shell-free. XDG
starts at desktop login but does not automatically restart a crashed worker.
Process pausing verifies /proc uid, argv and registered database before signalling.

Offline builder options: https://docs.flatpak.org/en/latest/flatpak-builder-command-reference.html

## Committing the first lock

The manual `.github/workflows/flatpak-lock.yml` runs on the default branch only,
provisions/verifies real installed SDK/Runtime commits on Ubuntu, uploads the lock,
and commits it onto a unique `ci/flatpak-lock-...` review branch. It never force-pushes
or changes the default branch. Merge the reviewed lock before regular Flatpak
release jobs; `--prepare` now fails without a lock unless `--bootstrap` is explicitly
supplied. The workflow has not been dispatched from this local non-Git workspace.
