# Orange Notes

**Orange Notes v0.2.3.** [Установщик Windows](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/Orange-Notes-Setup.exe) · [Портативный ZIP](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Windows-AMD64.zip) · [Все файлы релиза](https://github.com/kirill548/OrangeNotes/releases/tag/v0.2.3)

**Orange Notes — кроссплатформенное (универсальное) приложение для заметок с открытым исходным кодом.** Создавайте и редактируйте заметки без сети, связывайте их через `[[wiki-ссылки]]`, исследуйте граф знаний и синхронизируйте данные с доверенными устройствами.

Проект включает настольное приложение для Windows, Linux и macOS и мобильные клиенты для iOS и Android. Код настольного клиента находится в `app/`, мобильного в `orange_notes_mobile/`. Браузерный клиент в разработке.

## Платформы и готовность

| Платформа | Клиент / стек | Доступность |
|---|---|---|
| Windows x64 | Python 3.12, PySide6 / Qt6 | Установщик и портативный ZIP в [v0.2.3](https://github.com/kirill548/OrangeNotes/releases/tag/v0.2.3) |
| Linux x64 | Python 3.12, PySide6 / Qt6 | tar.gz, AppImage, DEB и Flatpak в v0.2.3 |
| macOS Intel / Apple Silicon | Python 3.12, PySide6 / Qt6 | Отдельные ZIP и DMG для каждой архитектуры в v0.2.3 |
| Android | Flutter / Dart, Kotlin | [Тестовый APK Android](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Android-debug.apk) |
| iOS 15+ | Flutter / Dart, Swift / UIKit | [Неподписанный IPA для iOS](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-iOS-unsigned.ipa) версии 0.2.3 (4). Требует подписи перед установкой |
| Web | Планируемый браузерный клиент | Web-host, браузерное хранилище и транспорт ещё не реализованы; готовая Web-сборка не заявляется |

Универсальное позиционирование описывает весь продукт. Наличие Flutter не означает, что мобильный код уже работает в браузере: текущие `dart:io`, SQLite и mTLS требуют отдельных Web-адаптеров. Паритет функций между клиентами достигается поэтапно.

## Возможности

- В настольном приложении обновлены значки, панель напоминаний и параметры помощника. Флажок «Показывать питомца» выводит пчёлку поверх рабочего стола. При полёте и перетаскивании заметки остаются свёрнутыми. Пчёлку можно скрыть через меню правой кнопки мыши.
- Локальное SQLite-хранилище, поиск, теги и Markdown-редактор.
- Wiki-ссылки, обратные ссылки и интерактивный граф заметок.
- Голосовой ввод через системный сервис распознавания; доступность офлайн зависит от ОС.
- P2P-синхронизация с UUID, LWW-CRDT, Outbox/Inbox и защищённым TLS 1.3/mTLS-транспортом.
- Мобильный помощник: запрос по явно прикреплённой заметке к десктопному Ollama через отдельный AI Relay. Без десктопа — локальный полнотекстовый поиск с явной пометкой «без генерации».

AI Relay пока требует настройки разработчиком. Отдельная LLM на телефоне не установлена. Успешный CI не заменяет проверку камеры, микрофона, переподписи и обмена по Wi-Fi на физическом iPhone.

## Архитектура и стек

| Слой | Реализация |
|---|---|
| Мобильный интерфейс | Flutter / Dart, Material 3, Riverpod |
| Android-оболочка | Gradle, Kotlin, Android SDK |
| iOS-оболочка | Xcode Runner, Swift / UIKit, Flutter plugins; iOS 15+ |
| Данные | sqflite / SQLite, FTS5 с FTS4 и обычным поиском как запасными вариантами |
| Связи и синхронизация | Wiki-index, LWW-CRDT, UUID, транзакционные очереди |
| Подключения | mTLS, проверка сертификата и SHA256-отпечатка, отдельные разрешения ИИ |
| Десктопный узел | Python 3.12 / PySide6, локальный Ollama, проверка Grounding |
| Web-направление | Браузерный UI и адаптеры хранения/сети — следующий этап; стек готового клиента пока не зафиксирован |
| Автоматизация | GitHub Actions: Python/Qt на четырёх ОС/архитектурах, Flutter analysis/tests, Android APK, unsigned iOS IPA |

**SwiftUI и React Native не используются.** Мобильный UI написан на Flutter, десктопный — на PySide6/Qt; Swift служит нативной точкой входа iOS. Мобильная БД — самостоятельная схема с совместимым протоколом синхронизации, а не копия десктопного SQLite-файла.

```text
orange_notes_mobile/
  lib/data/          SQLite, поиск и транзакции
  lib/domain/        Модели данных
  lib/services/      CRDT, P2P, AI gateway, голосовой ввод
  lib/presentation/  Список, редактор, граф, сопряжение, чат
  ios/               Xcode workspace и Swift Runner
  android/           Android host
  test/              Dart unit- и widget-тесты
app/                 Десктопное приложение и Python P2P/AI-сервисы
tests/               Python unit-, UI- и сетевые тесты
tools/               Сборка, диагностика, межъязыковые проверки
docs/                Архитектура и инструкции
.github/workflows/   CI и доставка
```

## Локальный запуск для разработчика

Проверенный инструментальный стек: Flutter **3.47.7**, Dart **3.13.5**. Для iOS нужны macOS, Xcode с iOS SDK и CocoaPods для текущего проекта. Подготовку окружения проверяйте через `flutter doctor -v`; ориентируйтесь на [официальную инструкцию Flutter для iOS](https://docs.flutter.dev/platform-integration/ios/setup).

```sh
git clone https://github.com/kirill548/OrangeNotes.git
cd OrangeNotes/orange_notes_mobile
flutter doctor -v
flutter pub get
dart format --output=none --set-exit-if-changed lib test
flutter analyze --fatal-infos
flutter test
flutter devices
flutter run -d <DEVICE_ID>
```

Для iOS Simulator выберите его идентификатор из `flutter devices`. Для физического телефона откройте `ios/Runner.xcworkspace` в Xcode, настройте собственные Team/Bundle ID и подпись; используйте workspace, а не `.xcodeproj`. Windows/Linux подходят для Dart-проверок и Android-разработки, но не для локальной сборки iOS.

### Desktop: запуск и сборка

Из корня репозитория на целевой ОС, с Python 3.12 x64:

```sh
python -m pip install -r requirements.txt
python app/main.py
# Для упаковки:
python -m pip install -r requirements-build.txt
python tools/build.py --clean --edition lite
```

Ссылки на пакеты и порядок сборки установщика описаны в [руководстве по установке](DISTRIBUTION.md).

### Android: запуск и тестовый APK

Из `orange_notes_mobile/`, с Android SDK и JDK 17:

```sh
flutter pub get
flutter devices
flutter run
flutter build apk --debug
```

APK появляется в `build/app/outputs/flutter-apk/app-debug.apk`. Это тестовая сборка; публикация магазина требует собственной конфигурации release-подписи.

### Web

Готового Web-host в репозитории пока нет. Команда `flutter build web` не является подтверждённым способом сборки Orange Notes; сначала нужны браузерные адаптеры и отдельная квалификация.

### iOS: сборка unsigned IPA на macOS

Из каталога `orange_notes_mobile/`:

```sh
flutter pub get
flutter build ios --release --no-codesign
mkdir -p build/unsigned/Payload
ditto build/ios/iphoneos/Runner.app build/unsigned/Payload/Runner.app
cd build/unsigned
zip -qry OrangeNotes-iOS-unsigned.ipa Payload
shasum -a 256 OrangeNotes-iOS-unsigned.ipa > SHA256SUMS.txt
```

Эти команды упаковывают неподписанную сборку для iPhone. Проверенные настольные и мобильные файлы публикуются через [сценарий выпуска](.github/workflows/publish-verified-release.yml).

## Установка и тестирование

### Desktop: Windows, Linux и macOS

Пакеты для Windows, Linux и macOS находятся в [Assets релиза v0.2.3](https://github.com/kirill548/OrangeNotes/releases/tag/v0.2.3). Для macOS выберите пакет под Intel или Apple Silicon.

Для установки скачайте файл `Orange-Notes-Setup.exe`. [Установщик Windows](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/Orange-Notes-Setup.exe) · [SHA256SUMS.txt](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/SHA256SUMS.txt).

В сборках с поддержкой обновлений новая версия появляется в нижней строке окна. Для проверки вручную выберите «…» → «Проверить обновления». [Обновление Windows и портативной копии](docs/DESKTOP_UI_GUIDE.md#обновление-и-смена-сборки-windows).

Запустите установщик и подтвердите запрос Windows. Затем в мастере выберите диск и папку, например `D:\Install\Orange Notes`. Путь по умолчанию — `C:\Program Files\Orange Notes`. Мастер создаст папку, если её ещё нет. Удаление доступно через «Установленные приложения» в параметрах Windows.

Для запуска без установки распакуйте `OrangeNotes-Windows-AMD64.zip` целиком и запустите `OrangeNotes.exe`. База заметок находится в `%LOCALAPPDATA%\OrangeNotes`. При обновлении и переносе сохраняйте папки `ai/` и `stt/`. [Руководство по установке](DISTRIBUTION.md).

### Android: тестовый APK

Скачайте [Тестовый APK Android](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-Android-debug.apk), передайте файл на телефон и откройте его. При запросе Android разрешите установку для выбранного источника.

### Web

Публичный браузерный клиент и ссылка для установки PWA пока не опубликованы.

### iOS: установка приложения (.ipa)

1. Скачайте [Неподписанный IPA для iOS](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/OrangeNotes-iOS-unsigned.ipa) из релиза v0.2.3.
2. Скачайте [SHA256SUMS.txt](https://github.com/kirill548/OrangeNotes/releases/download/v0.2.3/SHA256SUMS.txt) для сверки файла.
3. Сверьте SHA256: на macOS `shasum -a 256 OrangeNotes-iOS-unsigned.ipa`, в PowerShell `Get-FileHash ./OrangeNotes-iOS-unsigned.ipa -Algorithm SHA256`.
4. Выберите один способ подписи ниже. Простое открытие unsigned IPA на iPhone его не установит.

#### AltStore Classic

1. Установите AltServer на компьютер и AltStore Classic на iPhone по [официальной инструкции](https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows).
2. Передайте IPA в «Файлы» на iPhone. Откройте AltStore Classic → **My Apps**, нажмите **+** и выберите IPA; держите AltServer доступным во время установки.
3. Завершите подпись собственным Apple ID в выбранном инструменте. При запросе iOS подтвердите доверие профилю и включите режим разработчика.
4. Откройте Orange Notes. Для бесплатного аккаунта нужна периодическая переподпись; AltStore описывает срок 7 дней и обновление через **Refresh All** в [руководстве](https://faq.altstore.io/altstore-classic/your-altstore).

#### Sideloadly

1. Установите [Sideloadly](https://sideloadly.io/) на Windows/macOS. Подготовьте Apple-компоненты по инструкции программы, подключите iPhone по USB и подтвердите доверие компьютеру.
2. Выберите телефон, добавьте скачанный IPA и укажите собственный Apple ID. Нажмите **Start** и дождитесь завершения подписи и установки.
3. При необходимости подтвердите профиль в настройках iOS и включите **Developer Mode**. Условия обновления подписи и настройки Wi-Fi описаны в [официальном FAQ](https://sideloadly.io/faq).

#### Scarlet

1. Установите Scarlet по инструкции [сайта проекта](https://usescarlet.com/).
2. Убедитесь, что выбранный способ установки предоставляет действующий сертификат подписи; если требуется, импортируйте собственный сертификат и профиль.
3. Импортируйте IPA в Scarlet и запустите подпись/установку. Если сертификат недействителен или отозван, используйте другой доступный способ подписи.

Совместимость Orange Notes со Scarlet отдельно не проверена. Наличие unsigned IPA не предоставляет сертификат и не гарантирует установку без компьютера. Apple ID, пароли и приватные ключи не добавляются в исходники или обсуждения GitHub. Подробнее: [доставка на iPhone](docs/IOS_DELIVERY.md).

## Проверки и документация

В v0.2.2 прошли проверки сборок Windows x64, Linux x64, macOS Intel и Apple Silicon, мобильные тесты и сборки Android и iOS. При публикации сверены SHA-256 всех 13 файлов. [Настольные сборки](https://github.com/kirill548/OrangeNotes/actions/runs/45963684683) · [Мобильные сборки](https://github.com/kirill548/OrangeNotes/actions/runs/45963686341).

SQLite и резервные копии не зашифрованы на уровне приложения. Текущий протокол синхронизации ограничен 4096 сущностями и 8 МиБ истории; сохранение 50 000 локальных заметок не означает поддержку их полной синхронизации. Физические iOS/Android-устройства и Web-клиент этим аудитом не квалифицированы.

- [Оглавление документации](docs/README.md)
- [Обновлённый интерфейс Desktop и помощник](docs/DESKTOP_UI_GUIDE.md)
- [Журнал изменений](release_notes.md)
- [Описание v0.2.3](RELEASE_NOTES_v0.2.3.md)
- [Описание v0.2.3](RELEASE_NOTES_v0.2.3.md)
- [Описание v0.2.1](RELEASE_NOTES_v0.2.1.md)
- [Полный стресс-аудит и ограничения](TOTAL_STRESS_TEST_REPORT.md)
- [Спецификация мобильного клиента](docs/MOBILE_CLIENT_SPEC.md)
- [Архитектура P2P](docs/P2P_SYNC_ARCHITECTURE.md)
- [Мобильный AI Relay](docs/MOBILE_AI_RELAY.md)
- [План развития](ROADMAP.md)
- [Подготовка iOS-артефакта универсального приложения](docs/IOS_REPOSITORY_BOOTSTRAP.md)
- [Правила для AI-агентов](AGENTS.md)

## Лицензия

Исходный код Orange Notes распространяется по [MIT License](LICENSE). Сторонние библиотеки, модели и ассеты сохраняют собственные условия; уведомления в `licenses/` не заменяются лицензией проекта. Модели и приватные сертификаты в исходный репозиторий не включаются.
