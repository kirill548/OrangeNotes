# Документация Orange Notes

## Установка и работа

- [Пакеты и установка Desktop](../DISTRIBUTION.md)
- [Интерфейс Desktop и помощник](DESKTOP_UI_GUIDE.md)
- [Проверка установки](INSTALL_AND_SMOKE.md) и [поддерживаемые архитектуры](PLATFORM_SUPPORT.md)
- [Голосовой ввод](V014_VOICE_INPUT.md) и [портативные компоненты](V014_PORTABLE_COMPONENTS.md)
- [Мобильное приложение](../orange_notes_mobile/README.md), [установка на iPhone](IOS_DELIVERY.md) и [подготовка iOS-артефакта](IOS_REPOSITORY_BOOTSTRAP.md)
- [Журнал изменений](../release_notes.md) и [план развития](../ROADMAP.md)

## Архитектура

- [Локальный помощник](AI_ASSISTANT_ARCHITECTURE.md), [контекст выбранных заметок](NOTE_CONTEXT_ATTACHMENT.md) и [проектирование помощника](../AI_COMPANION_DESIGN.md)
- [Индекс связей](KNOWLEDGE_GRAPH_v0.1.5.md) и [раскладка графа и поиск по связям](KNOWLEDGE_GRAPH_PHASE2.md)
- [P2P](P2P_SYNC_ARCHITECTURE.md), [сетевой транспорт и очереди](P2P_NETWORK_PHASE2.md) и [мобильный прототип синхронизации](MOBILE_P2P_PROTOTYPE.md)
- [Спецификация мобильного клиента](MOBILE_CLIENT_SPEC.md) и [реализация Flutter](MOBILE_IMPLEMENTATION_v020.md)
- [AI Relay](MOBILE_AI_RELAY.md) и [методика проверки AI Relay](MOBILE_AI_BENCHMARK.md)

## Разработка и проверка

- [Правила репозитория](../AGENTS.md)
- [Стресс-аудит v0.2.1](../TOTAL_STRESS_TEST_REPORT.md) и [нативные проверки уведомлений](../tools/NATIVE_NOTIFICATION_TESTS.md)
- [Проверки Desktop](../.github/workflows/release-builds.yml) и [мобильных сборок](../.github/workflows/mobile-ci.yml)

## Исторические материалы

- [Разработка v0.1.2](DEVELOPMENT_v0.1.2.md)
- v0.1.3. [Редакции](V013_EDITIONS.md), [сравнение движков](V013_AI_ENGINE_COMPARISON.md), [сравнение моделей](V013_CANDIDATE_COMPARISON.md), [повторная проверка Qwen](V013_QWEN_REQUALIFICATION.md)
- [Аудит текстов v0.1.4](V014_TEXT_UX_AUDIT.md) и [аудит текстов v0.1.5](TEXT_UX_AUDIT_v0.1.5.md)
- [Общий отчёт QA](../QA_REPORT.md), [часовые пояса](../QA_TIMEZONES_2026-10-07.md), [проверка релиза](../QA_RELEASE_2026-10-08.md), [нативная интеграция](../QA_NATIVE_INTEGRATION_2026-10-08.md), [упаковка](../QA_NATIVE_PACKAGING_2026-10-08.md) и [статус релиза на 8 октября](../RELEASE_STATUS_2026-10-08.md)

Исторические показатели относятся к указанным версиям и коммитам. Проверку свежей сборки ищите в соответствующем запуске GitHub Actions.
