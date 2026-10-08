# Orange Notes release validation — 2026-10-08

Repository: https://github.com/kirill548/OrangeNotes (private).
Source, workflows and release-candidate tags are pushed. RC4 commit: 03ce8c4.

| Platform | Actual evidence | Remaining validation |
| --- | --- | --- |
| Windows x64 | RC4: 369 tests, 367 PASS, 2 SKIP; build and frozen startup smoke PASS | Unsigned; physical SmartScreen/banner smoke remains manual |
| Linux x64 | RC4: 369 tests, 362 PASS, 7 SKIP; native Dunst acceptance/removal, frozen startup and rootless Debian verification PASS | Physical desktop smoke remains manual |
| macOS Intel / Apple Silicon | RC4 each: 369 tests, 362 PASS, 7 SKIP; native Cocoa keyboard, .app build, frozen startup and SQLite integrity PASS | UserNotifications delivery remains unverified: cloud permission Denied; no Developer ID/notarization |
| Flatpak 25.08 lock | Exact installed commits verified; RC4 Flatpak bundle successfully built alongside AppImage/deb | Flatpak reminders run while application remains alive; physical sandbox smoke remains manual |

All four RC4 package jobs succeeded in GitHub run 37825268818.
These results do not certify physical display of macOS or Windows banners.

## Fixes driven by real CI
- Qt xcb runtime dependencies and fail-fast shared-library diagnostics.
- Bee bubble height follows real font metrics on Linux/macOS.
- Qt translator returns null for unknown strings, preserving native key parsing and translations.
- macOS permission rejection is explicitly classified as Denied.
- Older Linux libnotify clients use an acknowledged, closable D-Bus notification; action buttons are not claimed for that fallback.
- D-Bus activation inherits the test X display.
- Cross-platform path aliases, native event-loop waits and bounded SQLite contention checks.

## Genuine native macOS logs
work/mac-arm-native-notifications.log and work/mac-intel-native-notifications.log
contain actual cloud framework denial, not a passing banner test. Hosted API
acceptance would also not prove human-visible banners. No Developer ID or Apple
notarization credentials are configured; production Accepted cannot be claimed.

No fixture Debian package is published as an application. User databases, local
models, credentials, certificates and runtime logs are excluded from source.
The private repository prevents public downloads until its visibility is changed.
