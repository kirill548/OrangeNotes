# Orange Notes

**Orange Notes — современный open-source менеджер заметок для iOS.** Создавайте и редактируйте заметки без сети, связывайте их через `[[wiki-ссылки]]`, исследуйте граф знаний и синхронизируйте данные с доверенными устройствами.

Мобильный клиент развивается в `orange_notes_mobile/`. Этот репозиторий также содержит десктопное приложение и общее P2P-ядро. Текущая мобильная версия — **0.2.0**. `v1.0.0` в инструкции публикации — пример будущего первого релиза отдельного iOS-репозитория, а не уже выпущенная версия.

## Возможности

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
| iOS-оболочка | Xcode Runner, Swift / UIKit, Flutter plugins; iOS 15+ |
| Данные | sqflite / SQLite, FTS5 с FTS4 и обычным поиском как запасными вариантами |
| Связи и синхронизация | Wiki-index, LWW-CRDT, UUID, транзакционные очереди |
| Подключения | mTLS, проверка сертификата и SHA256-отпечатка, отдельные разрешения ИИ |
| Десктопный узел | Python 3.12 / PySide6, локальный Ollama, проверка Grounding |
| Автоматизация | GitHub Actions: Flutter analysis/tests, Android APK, unsigned iOS IPA |

**SwiftUI и React Native не используются.** Основной UI написан на Flutter; Swift служит нативной точкой входа iOS. Мобильная БД — самостоятельная схема с совместимым протоколом синхронизации, а не копия десктопного SQLite-файла.

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

### Сборка unsigned IPA на macOS

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

Это упаковка неподписанного device-бандла, не подписанный экспорт App Store. Готовые CI-пакеты находятся в артефактах workflow [Mobile analysis and native builds](.github/workflows/mobile-ci.yml). При публикации сопровождающий прикрепляет IPA и SHA256 к GitHub Release.

## Установка приложения (.ipa)

1. Откройте [GitHub Releases проекта](https://github.com/kirill548/OrangeNotes/releases), выберите нужную версию и разверните **Assets**.
2. Скачайте `OrangeNotes-iOS-unsigned.ipa` и `SHA256SUMS.txt`, если они прикреплены. Если IPA в релизе нет, используйте артефакт успешного Mobile CI — старые десктопные релизы не содержат мобильный пакет автоматически.
3. Сверьте SHA256: на macOS `shasum -a 256 OrangeNotes-iOS-unsigned.ipa`, в PowerShell `Get-FileHash ./OrangeNotes-iOS-unsigned.ipa -Algorithm SHA256`.
4. Выберите один способ подписи ниже. Простое открытие unsigned IPA на iPhone его не установит.

### AltStore Classic

1. Установите AltServer на компьютер и AltStore Classic на iPhone по [официальной инструкции](https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows).
2. Передайте IPA в «Файлы» на iPhone. Откройте AltStore Classic → **My Apps**, нажмите **+** и выберите IPA; держите AltServer доступным во время установки.
3. Завершите подпись собственным Apple ID в выбранном инструменте. При запросе iOS подтвердите доверие профилю и включите режим разработчика.
4. Откройте Orange Notes. Для бесплатного аккаунта нужна периодическая переподпись; AltStore описывает срок 7 дней и обновление через **Refresh All** в [руководстве](https://faq.altstore.io/altstore-classic/your-altstore).

### Sideloadly

1. Установите [Sideloadly](https://sideloadly.io/) на Windows/macOS. Подготовьте Apple-компоненты по инструкции программы, подключите iPhone по USB и подтвердите доверие компьютеру.
2. Выберите телефон, добавьте скачанный IPA и укажите собственный Apple ID. Нажмите **Start** и дождитесь завершения подписи и установки.
3. При необходимости подтвердите профиль в настройках iOS и включите **Developer Mode**. Условия обновления подписи и настройки Wi-Fi описаны в [официальном FAQ](https://sideloadly.io/faq).

### Scarlet

1. Установите Scarlet по инструкции [сайта проекта](https://usescarlet.com/).
2. Убедитесь, что выбранный способ установки предоставляет действующий сертификат подписи; если требуется, импортируйте собственный сертификат и профиль.
3. Импортируйте IPA в Scarlet и запустите подпись/установку. Если сертификат недействителен или отозван, используйте другой доступный способ подписи.

Совместимость Orange Notes со Scarlet отдельно не проверена. Наличие unsigned IPA не предоставляет сертификат и не гарантирует установку без компьютера. Apple ID, пароли и приватные ключи не добавляются в исходники или обсуждения GitHub. Подробнее: [доставка на iPhone](docs/IOS_DELIVERY.md).

## Проверки и документация

Проверенный коммит мобильного AI Relay: `c54d1b9`. [Mobile CI](https://github.com/kirill548/OrangeNotes/actions/runs/38926750683): 80 Flutter-тестов, анализатор, межъязыковые mTLS-проверки и обе мобильные сборки — PASS. [Desktop CI](https://github.com/kirill548/OrangeNotes/actions/runs/38926750839): Windows/Linux/macOS Intel/Apple Silicon — 0 FAIL; отдельные платформенные проверки пропущены, а не засчитаны как PASS.

- [Спецификация мобильного клиента](docs/MOBILE_CLIENT_SPEC.md)
- [Архитектура P2P](docs/P2P_SYNC_ARCHITECTURE.md)
- [Мобильный AI Relay](docs/MOBILE_AI_RELAY.md)
- [План развития](ROADMAP.md)
- [Инициализация отдельного репозитория и первый релиз](docs/IOS_REPOSITORY_BOOTSTRAP.md)
- [Правила для AI-агентов](AGENTS.md)

## Лицензия

Исходный код Orange Notes распространяется по [MIT License](LICENSE). Сторонние библиотеки, модели и ассеты сохраняют собственные условия; уведомления в `licenses/` не заменяются лицензией проекта. Модели и приватные сертификаты в исходный репозиторий не включаются.
