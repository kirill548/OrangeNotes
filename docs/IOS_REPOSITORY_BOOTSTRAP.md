# Подготовка iOS-артефактов универсального Orange Notes

Orange Notes — кроссплатформенный проект для Desktop, iOS, Android и будущего Web-клиента. Это руководство посвящено только сборке и публикации его iOS-артефакта; оно не превращает весь проект в iOS-эксклюзив. Для остальных платформ используйте [README](../README.md).

Раздел инициализации ниже — необязательный пример для **новой отдельной папки** и пустого кроссплатформенного репозитория `https://github.com/USERNAME/OrangeNotes.git`. Текущий `kirill548/OrangeNotes` уже имеет историю; команды инициализации к нему не применяются. Они не были выполнены при подготовке документации.

## 1. Подготовка исходников

Создайте новую папку и скопируйте в неё исходники проекта, документацию и CI без `.git`, `work/`, моделей, приватных данных и сборок. Сохраните структуру монорепозитория, если используете существующие Python↔Dart проверки: им нужны `app/`, `tools/` и fixtures, а не только Flutter-папка. Весь проект переносить или удалять из действующего репозитория не требуется.

Установите Git и GitHub CLI, создайте пустой `OrangeNotes` в своём GitHub-аккаунте. Замените `USERNAME` в примерах своим логином. Пример для PowerShell из новой подготовленной папки:

```powershell
if (Test-Path -LiteralPath .git) { throw 'В этой папке Git уже инициализирован' }
git init -b main
git status --short --untracked-files=all
# Просмотрите список: только исходники и публичные материалы.
git add --all
git diff --cached --stat
git commit -m "chore: initialize cross-platform Orange Notes"
git remote add origin https://github.com/USERNAME/OrangeNotes.git
gh auth login
git push -u origin main
```

Если репозиторий GitHub ещё не создан, вместо `git remote add origin` можно выполнить `gh repo create USERNAME/OrangeNotes --public --source . --remote origin`, затем `git push -u origin main`.

Обновите ссылки на репозиторий в README и документации под нового владельца. В копии workflow `mobile-ci.yml` добавьте `main` в `on.push.branches`: текущая конфигурация исходного проекта слушает `v0.2.0-dev`. Если переносите остальные workflows, проверьте их ветки, права и пути отдельно.

## 2. Сборка и проверка iOS-артефакта

1. Настройте Flutter 3.47.7 / Dart 3.13.5 и macOS/Xcode для iOS.
2. Перед фактическим `v1.0.0` установите согласованную версию, например `version: 1.0.0+1` в `orange_notes_mobile/pubspec.yaml`. Текущая исходная мобильная версия — `0.2.0+1`; инструкция не меняет её автоматически.
3. Пройдите анализатор, Flutter-тесты и необходимые Python↔Dart проверки.
4. Соберите IPA на macOS по README либо запустите Mobile CI и скачайте артефакт `OrangeNotes-iOS-unsigned-requires-resigning`.
5. Сверьте SHA256 и commit SHA сборки. Проверьте Info.plist, разрешения и установку на устройстве. Укажите в release notes реально выполненные проверки и ограничения.
6. Добавьте изменения версии и описание релиза в коммит `main`, отправьте его. Тег должен указывать именно на коммит, из которого собран IPA.

GitHub Actions и checksum не доказывают работу на физическом телефоне. Не называйте unsigned IPA подписанной Development/TestFlight-сборкой.

## 3. Тег общего релиза и публикация iOS-артефакта

Из чистого checkout `main`, соответствующего проверенному пакету:

```powershell
git switch main
git pull --ff-only origin main
git status --short
# Убедитесь, что HEAD совпадает с проверенным коммитом сборки.
git tag -a v1.0.0 -m "Orange Notes v1.0.0"
git push origin v1.0.0
```

Создайте содержательный `RELEASE_NOTES_v1.0.0.md`: возможности, минимальная iOS, способ установки, SHA коммита, проверки, известные ограничения. Это описание будущего релиза, а не копия отчёта о десктопной v0.1.5.

Прямая команда публикации (пути в этом примере относительные):

```powershell
gh release create v1.0.0 ./OrangeNotes-iOS-unsigned.ipa ./SHA256SUMS.txt --repo USERNAME/OrangeNotes --verify-tag --title "Orange Notes v1.0.0" --notes-file ./RELEASE_NOTES_v1.0.0.md
```

Указанное название релиза относится ко всему Orange Notes; IPA — один из возможных пакетов этого релиза. Скрипт работает только с iOS-пакетом и не собирает другие платформы. Дополнительные проверенные пакеты прикрепляются отдельно.

`--verify-tag` останавливает команду, если отправленного тега нет. IPA и checksum прикрепляются как Release Assets; сам IPA в git не коммитится.

## 4. Скрипт публикации iOS-пакета

Вместо прямой команды используйте [publish_ios_release.ps1](../tools/publish_ios_release.ps1). Скрипт проверяет имя и ZIP-структуру IPA, отсутствие отслеживания IPA в git и наличие описания. Затем создаёт SHA256 и вызывает `gh release create` для существующего тега.

```powershell
pwsh ./tools/publish_ios_release.ps1 -Repository USERNAME/OrangeNotes -IpaPath ./OrangeNotes-iOS-unsigned.ipa -NotesPath ./RELEASE_NOTES_v1.0.0.md -DryRun
# После просмотра результата запустите без -DryRun для реальной публикации.
pwsh ./tools/publish_ios_release.ps1 -Repository USERNAME/OrangeNotes -IpaPath ./OrangeNotes-iOS-unsigned.ipa -NotesPath ./RELEASE_NOTES_v1.0.0.md
```

Для черновика добавьте `-Draft`. Скрипт не меняет origin, не создаёт тег, не делает push и не перезаписывает существующий релиз. Проверка совпадения версии/коммита IPA с тегом остаётся обязанностью сопровождающего; исходный unsigned пакет не содержит доказательства происхождения только по имени файла.
