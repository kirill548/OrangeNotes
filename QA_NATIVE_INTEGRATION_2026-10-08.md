# Orange Notes · нативная интеграция, 8 октября 2026

## Подтверждённые результаты
Три агента: Linux/XDG, macOS/production signing, независимый QA/CI.

Windows: финальный полный прогон — 362 теста, 360 PASS, 2 SKIP, 62.776 с.
Пропуски относятся к реальным Linux/macOS уведомлениям. Отдельные fallback UI
10 PASS и worker 11 PASS. Журнал QA_NATIVE_FINAL_2026-10-08.log.
Промежуточный baseline запуск пересёкся с записью Mac-модуля и дал ошибки импорта;
после завершения всех правок финальный прогон полностью прошёл.

Ubuntu WSL2, x86_64, Python 3.12, uid1000:
- Упаковщик реально создал deb, verify_deb реально проверил его без root.
  Вход — тестовый shell-executable и иконка, НЕ готовое Qt Linux-приложение.
- UTC recovery 11 PASS; scheduler clock 5 PASS; scheduler edges 7 PASS.
- Worker 7 PASS / 4 SKIP (платформенные случаи).
- XDG 7 PASS; исполняемые аварийные тесты harness 2 PASS; lock safety 5 PASS.
  Итого 48 тестов: 44 PASS, 4 SKIP. Полный Qt suite на Linux не запускался.
- Для UTC suite локально скопированы установленные pure-Python tzlocal/tzdata;
  первый запуск без tzlocal не прошёл из-за отсутствующей зависимости.
- Журналы: work/native-linux/deb-verification.log, core-tests.log, infra-tests.log.

## Lock SDK/Runtime
Реальный runtime-lock.json получен Python/curl из Ubuntu WSL по HTTPS:
org.freedesktop.Platform/x86_64/25.08:
d27f7a6a974e40b061070bec1be9e1b52a7a6872b271e6a45c2c34a48bf6fedf
org.freedesktop.Sdk/x86_64/25.08:
0cc82216a407cc993941b5ddabd446becc3c9a6219d9bcf50125c60101dcad46

Источник и время: packaging/flatpak/runtime-lock.provenance.json.
Уточнение проверки сети: индекс /repo/ отвечает HTTP403, но /repo/config и
реальные refs отвечают HTTP200; Flathub не объявляется заблокированным.
SDK/Runtime здесь не установлены клиентом Flatpak; подписи OSTree/GPG и сборка
пакета ещё не проверены. При --prepare настоящие установленные коммиты должны
совпасть с lock. Невалидный, пустой и изменившийся lock отклоняются; запись атомарная.

Git-репозитория/remote в рабочей папке нет, пользователь не знает настроенного CI.
Файл создан, но Git-коммит НЕ выполнен. Добавлен ручной workflow flatpak-lock.yml:
на Ubuntu он устанавливает/проверяет закреплённые ревизии, сохраняет артефакт и
коммитит lock в отдельную ветку без force-push/изменения основной ветки.
Этот workflow не запускался и не заменяет состоявшийся коммит.

## Доработки
Linux: безопасный XDG Autostart при недоступном systemd --user, без root и shell,
с защитой чужих файлов/симлинков; проверка /proc uid, argv и базы перед сигналом.
Не скрывает PermissionDenied. XDG стартует при входе в desktop, но автоматически
не перезапускает аварийно завершённый worker. Пропущенное сохраняется в SQLite.

macOS: до120с на первичный ответ пользователя, до5с на доставку; опрос системных
настроек выявляет Denied и отзыв прав. notarytool JSON должен содержать Accepted
перед stapling. Ad-hoc runner сохраняет журнал вне временного .app.
Пять macOS контрактных тестов входят в Windows suite; это не реальные баннеры.

CI: исправлены кавычки shell harness; отсутствие D-Bus/отказ Dunst реально
исполнены в тестах с временными адаптерами и возвращают неуспешный exit.
GitLab пакетирует от отдельного непривилегированного пользователя.
Оба macOS job запускают .app suite, затем production probe и сохраняют журнал.

## Windows-сборка
EXE пересобран, UNSIGNED (сертификат не предоставлен).
smoke_frozen --hostile-qt-env PASS: окно с иконкой, worker heartbeat без ошибки,
SQLite integrity=ok, foreign_key_errors=0, отдельная временная база.
Лог smoke-native-final-2026-10-08.log. Пользовательские заметки не использовались
в тестах, локальный ИИ-комплект сохранён отдельно от ZIP.

## Незавершённая внешняя валидация
Нет физического Mac/Intel/AppleSilicon или подключённого self-hosted runner.
Успешного mac-native-notifications.log с физического Mac НЕ получено.
Нет Dunst/Xvfb/Flatpak в WSL; настоящие нативные баннеры и готовый Flatpak/AppImage
не проверены. Полностью зелёный отчёт по всем целевым ОС не заявляется.
Следующий шаг — подключить Git-репозиторий и macOS/Linux desktop runners;
исполнить предусмотренные native jobs и проверить видимые баннеры/действия.
