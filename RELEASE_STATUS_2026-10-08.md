# Orange Notes v0.1.0-rc.1

Release candidate; not a claim of complete native validation.

| Platform | Confirmed locally | Native release checks |
| --- | --- | --- |
| Windows x64 | 360 PASS, 2 SKIP; frozen GUI/icon/worker/SQLite smoke PASS | unsigned ZIP ready; certificate not supplied |
| Linux Ubuntu WSL2 | 44 PASS, 4 SKIP; fixture deb packaging without root; real SDK/Runtime branch refs | desktop Qt/Dunst/Flatpak/AppImage checks pending CI |
| macOS Intel / ARM | 5 macOS contract checks pass on Windows | cloud bundle tests and signing/notarization not yet confirmed |

Real Flatpak 25.08 commits are tracked in packaging/flatpak/runtime-lock.json;
provenance records HTTPS ref resolution via WSL, not installed OSTree validation.
The initial source commit excludes local databases, models, certificates and logs.

Repository: https://github.com/kirill548/OrangeNotes (private).
CI results and downloadable artifacts will be recorded after actual workflow runs.
Developer ID and Apple notarization credentials have not been supplied; production
codesign/notarytool Accepted cannot be claimed. API acceptance tests do not prove
that a person saw a banner. No fixture.deb is a distributable Linux application.
