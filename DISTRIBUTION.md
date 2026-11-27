# Установка и сборка Orange Notes Desktop

Готовые пакеты и [SHA256SUMS.txt](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/SHA256SUMS.txt) доступны в [релизе v0.2.3](https://github.com/kirill548/OrangeNotes/releases/tag/v0.2.3).

- **Windows x64.** [Установщик Windows](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/Orange-Notes-Setup.exe) · [Портативный ZIP](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Windows-AMD64.zip).
- **Linux x64.** [tar.gz](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Linux-x86_64.tar.gz) · [AppImage](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Linux-x86_64.AppImage) · [DEB](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Linux-x64.deb) · [Flatpak](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Linux-x86_64.flatpak).
- **macOS Intel.** [DMG](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-macOS-x86_64.dmg) · [ZIP](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Darwin-x86_64.zip).
- **macOS Apple Silicon.** [DMG](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-macOS-arm64.dmg) · [ZIP](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Darwin-arm64.zip).
- **Мобильные устройства.** [Тестовый APK Android](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Android-debug.apk) · [Неподписанный IPA для iOS](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-iOS-unsigned.ipa). [Подпись и установка на iPhone](docs/IOS_DELIVERY.md).

## Пакеты и переносимость

| Платформа | Формат | Запуск |
|---|---|---|
| Windows x64 | Установщик EXE и портативный ZIP | Запустите мастер или распакуйте ZIP целиком и откройте `OrangeNotes.exe` |
| Linux x64 | tar.gz, AppImage, DEB, Flatpak | Распакуйте tar.gz с правами доступа и запустите `./OrangeNotes` |
| macOS Intel / Apple Silicon | `.app` в ZIP, DMG | Выберите свою архитектуру, перенесите `.app` в доступное для записи место |

Windows x86 и Windows 7/8 не поддерживаются стеком проекта. Intel и Apple Silicon собираются отдельно, Universal не заявляется. Linux требует системные библиотеки Qt/X11/OpenGL; совместимость со всеми дистрибутивами не обещается. Для Qt Multimedia необходимы доступный аудиобэкенд и разрешение на микрофон. Их отсутствие обрабатывается в настройках без падения приложения.

## Установка на Windows

1. Скачайте [Установщик Windows](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/Orange-Notes-Setup.exe) и сверьте SHA-256 по [SHA256SUMS.txt](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/SHA256SUMS.txt).
2. Запустите установщик и подтвердите запрос Windows. Укажите диск и папку приложения, например `D:\Install\Orange Notes`. Мастер проверит доступ и создаст нужные каталоги. Место сохранения скачанного EXE выбирается в браузере.
3. Для обновления используйте кнопку «Обновление» в нижней строке или повторно запустите установщик и выберите прежнюю папку. Кнопка доступна в сборках со встроенной проверкой GitHub Releases. При ручном обновлении ранней тестовой версии, установленной только для одного пользователя, запустите мастер с параметром `/CURRENTUSER`. Модели, настройки ИИ и голосовые компоненты сохраняются в `ai/` и `stt/`. Для их загрузки из приложения текущей учётной записи нужен доступ на запись в эти каталоги. База в `%LOCALAPPDATA%\OrangeNotes` остаётся на месте.
4. Для удаления откройте «Установленные приложения» в параметрах Windows и выберите Orange Notes. Перед удалением отдельно скопируйте заметки, `ai/` и `stt/`, если хотите сохранить их для переноса.

Для запуска без мастера возьмите отдельный `OrangeNotes-Windows-AMD64.zip`. Распакуйте весь архив вместе с `_internal/` и запустите `OrangeNotes.exe`.

## Данные и переносимость

Управляемые настройки ИИ, среда выполнения и модели находятся в `AppDir/ai/`, whisper.cpp и веса — в `AppDir/stt/`. В Windows/Linux AppDir — папка исполняемого файла; в macOS — `OrangeNotes.app/Contents/MacOS/`. Lite содержит шаблонные каталоги без моделей. Не удаляйте эти папки при обновлении. База заметок и обычные пользовательские настройки хранятся отдельно в системной папке данных (Windows: `%LOCALAPPDATA%/OrangeNotes`); перенос папки приложения сам по себе не переносит базу.

Установка компонентов требует записи в AppDir. Обычный AppImage смонтирован только для чтения: используйте tar.gz либо извлечённую папку AppImage. Изменение подписанного macOS-бандла может нарушить подпись; текущие неподписанные пакеты не заявляют нотаризацию. При переносе повторно откройте приложение из нового места, чтобы обновить регистрацию фоновых напоминаний.

В Windows повторный запуск того же EXE показывает уже открытое окно. Если запустить копию из другой папки с той же базой, она дождётся выхода работающего экземпляра. Выберите «…» → «Выйти из приложения» или «Выход» в меню значка рядом с часами. [Руководство по интерфейсу](docs/DESKTOP_UI_GUIDE.md).

## ИИ и диктовка

Откройте «Настройки ИИ», подключите локальный Ollama или управляемый AI-pack. Системный Ollama остаётся внешним подключением: его модели не удаляются менеджером приложения. Lite не включает веса ИИ, а Complete с утверждённой моделью по умолчанию пока не заявляется.

На вкладке «Голосовой ввод» выберите микрофон и скачайте пакет. Модели Whisper tiny/base/small позволяют выбрать баланс памяти и точности. Загруженные компоненты проверяются по SHA256. Записанный текст вставляется в поле помощника; перед отправкой проверьте слова, дату и время. Сохранение напоминания требует подтверждения черновика. Распознавание и ИИ работают локально, загрузка компонентов требует интернета.

## Сборка на целевой ОС

Используйте Python 3.12 x64, зависимости из `requirements-build.txt` и `requirements-dev.txt`:

```bash
python -m pip install -r requirements-build.txt -r requirements-dev.txt
python tools/run_regression.py
python tools/build.py --clean --edition lite
```

Чтобы собрать установщик на Windows с установленным Inno Setup, после сборки Lite выполните:

```powershell
python tools/build_windows_installer.py --bundle dist/OrangeNotes --output-dir dist
```

Готовый файл появится в `dist/Orange Notes Setup.exe`. Сборка установщика не заменяет portable ZIP.

PyInstaller собирает пакет на соответствующей ОС, не выполняет кросс-компиляцию. Linux AppImage и macOS DMG подготавливаются платформенными шагами CI. Конфигурация содержит Windows x64, Linux x64, macOS Intel и Apple Silicon. Для диагностики нативного whisper.cpp: `python tools/stt_native_smoke.py --help`; для Apple CLI/CMake: `python tools/diagnose_whisper_macos.py --help`.

`python scripts/deploy_local.py --edition lite --target <каталог>` обновляет управляемую установку с сохранением базы, настроек и локальных компонентов. Перед обновлением закройте приложение и сделайте резервную копию базы. Контрольные суммы пакетов публикуются в `SHA256SUMS.txt`.

## Публикация релиза

1. Подготовьте версии Desktop и Flutter, описание выпуска и черновик GitHub Release. Укажите полный SHA исходного коммита в качестве цели черновика.
2. Дождитесь успешных запусков Desktop package qualification и Mobile analysis and native builds для того же коммита.
3. Запустите Publish verified desktop and mobile packages из основной ветки. Передайте тег черновика и идентификаторы двух запусков. Сценарий проверит исходный коммит, архивы и версии, загрузит пакеты и сравнит их SHA-256 с данными GitHub. После сверки он поставит тег и опубликует черновик. [Сценарий публикации](.github/workflows/publish-verified-release.yml).

## Уведомления и лицензии

Компьютер должен работать, пользователь должен войти в систему. Windows использует пользовательскую фоновую задачу; Linux — systemd --user/XDG Autostart, macOS — launchd. Видимость баннеров зависит от разрешений ОС и режима «Не беспокоить». Проверки на виртуальных runner не гарантируют визуальную доставку на каждом устройстве. Дополнительно: [нативные проверки уведомлений](tools/NATIVE_NOTIFICATION_TESTS.md).

Сборки без сертификатов остаются неподписанными. Qt/PySide распространяются с лицензиями компонентов. Иконки Phosphor используются по [MIT](licenses/Phosphor-Icons-MIT.txt). Условия сторонних моделей проверяйте отдельно. Не публикуйте локальные базы, модели, журналы, токены и пользовательские `ai/`/`stt/` в исходном репозитории.
