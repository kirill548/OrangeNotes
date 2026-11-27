# Orange Notes v0.2.2

## Что изменилось

- На Windows x64 появился установщик `Orange-Notes-Setup.exe`. Он позволяет выбрать диск и папку и создаёт ярлыки. Для запуска без установки есть портативный ZIP.
- В настольном приложении обновлены значки, панель напоминаний и параметры помощника. Кнопка чата и флажок показа питомца работают независимо.
- Пчёлка остаётся на рабочем столе при свёрнутых заметках, поворачивается боком и совершает короткий полёт рядом с местом посадки. Перетащите её мышью или скройте через меню правой кнопки.
- Исправлена блокировка при сохранении заметки во время фоновой индексации. Файлы Linux и macOS собираются отдельно для каждой поддерживаемой платформы и архитектуры.
- На Android и iOS обновлены значок и окно чата, включая расположение элементов с учётом Safe Area.

## Какой файл скачать

- **Windows x64.** Запустите [Установщик Windows](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.2/Orange-Notes-Setup.exe) или распакуйте [Портативный ZIP](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.2/OrangeNotes-Windows-AMD64.zip) и откройте `OrangeNotes.exe`.
- **Linux x64.** Доступны tar.gz, AppImage, DEB и Flatpak.
- **macOS.** Выберите DMG или ZIP для Intel либо Apple Silicon.
- **Android.** [Тестовый APK Android](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.2/OrangeNotes-Android-debug.apk) можно установить вручную.
- **iOS.** [Неподписанный IPA для iOS](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.2/OrangeNotes-iOS-unsigned.ipa) версии 0.2.2 (3) требует подписи перед установкой. [Инструкция](https://github.com/kirill548/OrangeNotes/blob/main/docs/IOS_DELIVERY.md).

Сверьте скачанные файлы по [SHA256SUMS.txt](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.2/SHA256SUMS.txt).

## Проверки

Прошли четыре настольные сборки, мобильные тесты и сборки Android и iOS. При публикации сверены SHA-256 всех 13 файлов. Проверка системных уведомлений macOS на отдельном устройстве была пропущена. [Настольные сборки](https://github.com/kirill548/OrangeNotes/actions/runs/45963684683) · [Мобильные сборки](https://github.com/kirill548/OrangeNotes/actions/runs/45963686341).
