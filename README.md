# Orange Notes

Локальное приложение заметок на Python 3.12, PySide6 и SQLite. Папки, метки, поиск, чек-листы, напоминания и локальный ИИ-помощник. Данные пользователя не входят в исходники или пакеты.

Установка зависимостей: `python -m pip install -r requirements.txt`

Запуск: `python -m app.main`

Тесты: `python -m unittest discover -s tests`

Сборка на целевой ОС: установите `requirements-build.txt`, затем `python tools/build.py --clean`. Нативные пакеты Windows/Linux/macOS собирает GitHub Actions; пакеты других ОС нельзя считать проверенными до успешного запуска соответствующего задания и проверки на реальном устройстве.

Совместимость, передача приложения, ограничения служб и лицензирование: [DISTRIBUTION.md](DISTRIBUTION.md).
