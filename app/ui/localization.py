"""Russian Qt dialogs, including builds without Qt translation catalogs."""
from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator


class DialogTranslator(QTranslator):
    LABELS = {
        '&Yes': 'Да', 'Yes': 'Да', '&No': 'Нет', 'No': 'Нет',
        'OK': 'ОК', '&OK': 'ОК', 'Cancel': 'Отмена', '&Cancel': 'Отмена',
        'Save': 'Сохранить', '&Save': 'Сохранить',
        'Discard': 'Не сохранять', '&Discard': 'Не сохранять',
        "Don't Save": 'Не сохранять', "Do not Save": 'Не сохранять',
        'Close': 'Закрыть', '&Close': 'Закрыть',
        'Open': 'Открыть', '&Open': 'Открыть',
    }

    def isEmpty(self):
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if context in ('QPlatformTheme', 'QDialogButtonBox', 'QMessageBox', 'QFileDialog', 'QInputDialog'):
            return self.LABELS.get(sourceText)
        # None becomes a null QString: Qt must continue to other translators.
        # An empty string is a successful empty translation, breaking native
        # shortcut parsing (Ctrl/Meta) and platform-specific dialog labels.
        return None


def install_russian_dialogs(app):
    if hasattr(app, '_orange_translators'):
        return
    QLocale.setDefault(QLocale(QLocale.Russian))
    catalog = QTranslator(app)
    catalog.load('qtbase_ru', QLibraryInfo.path(QLibraryInfo.TranslationsPath))
    app.installTranslator(catalog)
    fallback = DialogTranslator(app)
    app.installTranslator(fallback)
    # Qt does not take ownership of installed translators.
    app._orange_translators = (catalog, fallback)
