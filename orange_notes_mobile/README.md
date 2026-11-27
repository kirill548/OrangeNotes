# Orange Notes Mobile

Flutter 3.47.7 / Dart 3.13.5. Offline-first Android/iOS prototype.

```text
flutter pub get
flutter analyze --fatal-infos
flutter test
flutter run
```

Notes are saved locally without a server. Tags are local to this device in wire protocol v1.
Desktop HTML bodies are preserved and shown read-only; newly created Markdown notes are editable.
SQLite local integer IDs map to UUIDv4 identities; wire deltas follow the desktop LWW protocol.
The mobile database is not a drop-in copy of the desktop database.

The [v0.2.2 release](https://github.com/kirill548/OrangeNotes/releases/tag/v0.2.2) includes the Android test APK, unsigned iOS IPA, and SHA-256 checksums. Use the [Mobile CI run](https://github.com/kirill548/OrangeNotes/actions/workflows/mobile-ci.yml) for builds from other commits.

See [implementation](../docs/MOBILE_IMPLEMENTATION_v020.md) and
[iOS delivery](../docs/IOS_DELIVERY.md). Unsigned IPA requires re-signing before installation.
No Apple credentials or production identity keys are included.
