"""Huge documents must not enter synchronous Qt layout or overwrite originals."""
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from app.database.store import Store
from app.ui.window import Window, MAX_RICH_DOCUMENT_CHARS, large_document_preview


def test_large_preview_is_bounded_and_visible():
    text = '<p>Начало</p>' + 'x' * (MAX_RICH_DOCUMENT_CHARS + 1)
    preview = large_document_preview(text)
    assert len(preview) < 300
    assert 'Начало' in preview
    assert 'Большая заметка' in preview


def test_large_open_preserves_editor_database_and_full_search(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'notes.sqlite3')
    small = store.create_note()
    store.save_note(small, 'Small', '<p>Keep me</p>', None, False, '')
    window = Window(store, background_managed=True)
    window.clock.stop()
    window.load_note(small)
    large = store.create_note()
    original = '<p>' + 'x' * (MAX_RICH_DOCUMENT_CHARS + 1) + ' distantneedle</p>'
    store.save_note(large, 'Large', original, None, False, '')
    try:
        with patch('app.ui.window.QMessageBox.warning') as warning:
            window.refresh_list()
            before = window.body.toHtml()
            with patch.object(window.body, 'setHtml', side_effect=AssertionError('must not layout oversized text')):
                assert window.load_note(large) is False
            assert window.current == small
            assert window.body.toHtml() == before
            assert not window._dirty
            warning.assert_called_once()
            assert window.save(refresh=False)
            assert store.rows('SELECT body FROM notes WHERE id=?', (large,))[0][0] == original
            # Text after the bounded card preview remains searchable.
            window.search.setText('distantneedle')
            window.refresh_list()
            assert window.notes.count() == 1
    finally:
        window.tray.hide()
        window.quitting = True
        window.close()
        window.deleteLater()
        app.processEvents()
        store.db.close()
