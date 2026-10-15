# Доставка Orange Notes на iPhone

Статус: инфраструктура сборки подготовлена. Наличие workflow не означает успешную сборку, подпись, загрузку в TestFlight или проверку на физическом iPhone. Результаты конкретного запуска фиксируются отдельно.

## Без аккаунта Apple Developer

Workflow [mobile-ci.yml](../.github/workflows/mobile-ci.yml) на `macos-latest` выполняет `flutter build ios --release --no-codesign`. Затем помещает `Runner.app` в `Payload/` и создаёт `OrangeNotes-iOS-unsigned.ipa` с SHA256. Это сборка для устройства, а не симулятора.

Артефакт **не подписан и не устанавливается напрямую**. Для AltStore/Sideloadly нужна повторная подпись собственным Apple ID и соблюдение ограничений выбранного инструмента и аккаунта. Совместимость этого приложения с такими инструментами и работа разрешений камеры, микрофона и локальной сети на телефоне ещё требуют ручной проверки. CI не получает Apple ID пользователя.

## Подписанная Development-сборка и TestFlight

Ручной workflow [mobile-ios-delivery.yml](../.github/workflows/mobile-ios-delivery.yml) использует защищённое GitHub Environment `ios-delivery`. Рекомендуется включить обязательное одобрение запуска и разрешить только доверенную ветку. Workflow не запускается из pull request и не хранит ключи в репозитории.

Переменные Environment:

| Имя | Значение |
| --- | --- |
| `APPLE_TEAM_ID` | Team ID владельца приложения |
| `IOS_BUNDLE_ID` | Зарегистрированный точный Bundle ID, совпадающий с профилем |

Secrets Environment:

| Имя | Назначение |
| --- | --- |
| `IOS_CERTIFICATE_P12_BASE64` | Сертификат с приватным ключом, экспортированный в P12 и Base64 |
| `IOS_CERTIFICATE_PASSWORD` | Пароль P12 |
| `IOS_PROVISION_PROFILE_BASE64` | Base64 профиля нужного типа |
| `IOS_KEYCHAIN_PASSWORD` | Отдельный случайный пароль временной связки ключей |
| `APPSTORE_API_PRIVATE_KEY_BASE64` | Base64 ключа App Store Connect `.p8`, только для TestFlight |
| `APPSTORE_API_KEY_ID` | ID ключа API, только для TestFlight |
| `APPSTORE_API_ISSUER_ID` | Issuer ID, только для TestFlight |

Для режима `development` требуются Apple Development certificate и профиль с зарегистрированным UDID телефона. Для `testflight` требуются Apple Distribution certificate, App Store distribution profile, приложение в App Store Connect и ключ API с достаточными правами. Выберите уникальный возрастающий `build_number`. Истёкший профиль, несовпадение Team/Bundle ID и неподходящий тип профиля останавливают workflow.

Подпись создаётся через временную связку ключей; материалы удаляются шагом `always()`. Успешный ответ загрузки означает приём пакета App Store Connect, **не** завершённую обработку Apple и **не** автоматическую доступность тестировщикам. Добавление тестировщиков, возможная Beta App Review и установка проверяются владельцем аккаунта.

В этом этапе аккаунт Apple, сертификаты и профили не предоставлены. Подписанный запуск и TestFlight не подтверждены. Запрашивать или присылать приватные ключи в чат не требуется: они добавляются владельцем напрямую в GitHub Secrets.

Официальные инструкции: [Flutter iOS deployment](https://docs.flutter.dev/deployment/ios), [GitHub signing on macOS runners](https://docs.github.com/en/actions/how-tos/deploy/deploy-to-third-party-platforms/sign-xcode-applications).
