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

Download the [Android test APK](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.2/OrangeNotes-Android-debug.apk), [unsigned iOS IPA](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.2/OrangeNotes-iOS-unsigned.ipa), or [SHA-256 checksums](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.2/SHA256SUMS.txt). [Release build and tests](https://github.com/kirill548/OrangeNotes/actions/runs/45963686341).

See [implementation](../docs/MOBILE_IMPLEMENTATION_v020.md) and
[iOS delivery](../docs/IOS_DELIVERY.md). Unsigned IPA requires re-signing before installation.
No Apple credentials or production identity keys are included.
