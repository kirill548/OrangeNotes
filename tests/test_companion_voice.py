"""Voice UI: mocked capture, no microphone/model/network required."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.companion import CompanionDialog

class FakeVoice(QObject):
    text_ready=Signal(str)
    state_changed=Signal(str)
    error=Signal(str)
    finished=Signal()
    def __init__(self,**kwargs):
        super().__init__(kwargs.get('parent'));self.cancelled=False;self.stopped=False
    def start(self):self.state_changed.emit('recording')
    def stop(self):self.stopped=True;self.state_changed.emit('transcribing')
    def cancel(self):self.cancelled=True


_application=None

def make_dialog(tmp_path):
    global _application
    _application=QApplication.instance() or QApplication([])
    store=Store(tmp_path/'notes.sqlite3')
    with patch('app.ui.companion.VoiceInputController',FakeVoice):
        dialog=CompanionDialog(store,engine_factory=lambda path:None)
    return dialog,store


def test_voice_appends_without_auto_send(tmp_path):
    dialog,store=make_dialog(tmp_path)
    try:
        dialog.input.setPlainText('Мой текст');dialog._toggle_voice()
        assert dialog.status.text()=='Слушаю…'
        assert not dialog.send_button.isEnabled()
        dialog._toggle_voice();assert dialog.voice_input.stopped
        assert dialog.status.text()=='Распознаю…'
        dialog.voice_input.text_ready.emit('купить молоко')
        dialog.voice_input.finished.emit()
        assert dialog.input.toPlainText()=='Мой текст\nкупить молоко'
        assert dialog._thread is None
        assert dialog.send_button.isEnabled()
    finally:dialog.close();store.db.close()


def test_cancel_discards_late_transcript(tmp_path):
    dialog,store=make_dialog(tmp_path)
    try:
        dialog.input.setPlainText('Оставить');dialog._toggle_voice();dialog.cancel()
        assert dialog.voice_input.cancelled
        dialog.voice_input.text_ready.emit('Устаревший текст');dialog.voice_input.finished.emit()
        assert dialog.input.toPlainText()=='Оставить'
        assert not dialog.busy
    finally:dialog.close();store.db.close()


def test_new_conversation_discards_late_transcript(tmp_path):
    dialog,store=make_dialog(tmp_path)
    try:
        dialog._toggle_voice();dialog.new_conversation()
        dialog.voice_input.text_ready.emit('старый разговор');dialog.voice_input.finished.emit()
        assert dialog.input.toPlainText()==''
    finally:dialog.close();store.db.close()


def test_missing_microphone_error_is_visible(tmp_path):
    dialog,store=make_dialog(tmp_path)
    try:
        dialog._toggle_voice();dialog.voice_input.error.emit('Микрофон недоступен');dialog.voice_input.finished.emit()
        assert dialog.status.text()=='Микрофон недоступен'
        assert dialog.send_button.isEnabled()
    finally:dialog.close();store.db.close()


def test_capture_start_exception_restores_ui(tmp_path):
    dialog,store=make_dialog(tmp_path)
    try:
        with patch.object(dialog.voice_input,'start',side_effect=RuntimeError('driver failure')):
            dialog._toggle_voice()
        assert not dialog.busy
        assert dialog.send_button.isEnabled()
        assert 'Не удалось начать запись' in dialog.status.text()
    finally:dialog.close();store.db.close()


def test_close_discards_late_transcript(tmp_path):
    dialog,store=make_dialog(tmp_path)
    try:
        dialog._toggle_voice();dialog.close()
        assert dialog.voice_input.cancelled
        dialog.voice_input.text_ready.emit('после закрытия');dialog.voice_input.finished.emit()
        assert dialog.input.toPlainText()==''
        assert not dialog.busy
    finally:dialog.close();store.db.close()
