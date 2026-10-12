"""Offline UI contract: worker affinity, cancel lifetime and manual import."""
import threading
import time
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from app.ui.stt_settings import STTSettingsTab
from app.widgets.ai_pack_settings import AIPackSettingsDialog

_app = None

def application():
    global _app
    _app = QApplication.instance() or QApplication([])
    return _app

def wait(predicate):
    app=application(); deadline=time.monotonic()+3
    while not predicate() and time.monotonic()<deadline:
        app.processEvents(); time.sleep(.005)
    assert predicate()

class Manager:
    def __init__(self):self.ready=False;self.thread=None;self.block=False;self.imported=None;self.verify_threads=[]
    def status(self):
        self.verify_threads.append(threading.get_ident())
        return dict(ready=self.ready,supported=True,message='stt/')
    def install(self,cancel_event,progress):
        self.thread=threading.get_ident();progress(50,100)
        while self.block and not cancel_event.is_set():time.sleep(.005)
        self.ready=not cancel_event.is_set()
    def save_manual_paths(self,executable,model):self.imported=(executable,model);self.ready=True

def test_installer_runs_off_ui_thread_and_refreshes():
    application();manager=Manager();tab=STTSettingsTab(manager);wait(lambda:not tab.busy);seen=[]
    tab.configured.connect(lambda:seen.append(True));tab.install();wait(lambda:not tab.busy)
    assert manager.thread!=threading.get_ident()
    assert seen==[True]
    tab.refresh();wait(lambda:not tab.busy);assert 'готовы к работе' in tab.status_label.text()
    assert manager.verify_threads and all(ident!=threading.get_ident() for ident in manager.verify_threads)
    tab.close()

def test_close_cancels_worker_and_preserves_lifetime():
    application();manager=Manager();manager.block=True
    dialog=AIPackSettingsDialog();tab=STTSettingsTab(manager,dialog);dialog.stt_tab=tab;wait(lambda:not tab.busy)
    tab.idle.connect(dialog.finish_pending_close);dialog.show();tab.install();dialog.close()
    assert dialog._close_pending
    wait(lambda:not tab.busy);wait(lambda:not dialog.isVisible())
    assert not manager.ready

def test_manual_selection_imports_both_files():
    application();manager=Manager();tab=STTSettingsTab(manager);wait(lambda:not tab.busy)
    with patch('app.ui.stt_settings.QFileDialog.getOpenFileName',side_effect=[('/external/whisper-cli',''),('/external/ggml-tiny.bin','')]):
        tab.choose_manual()
    wait(lambda:not tab.busy)
    assert manager.imported==('/external/whisper-cli','/external/ggml-tiny.bin')
    tab.close()

def test_manual_cancel_changes_nothing():
    application();manager=Manager();tab=STTSettingsTab(manager);wait(lambda:not tab.busy)
    with patch('app.ui.stt_settings.QFileDialog.getOpenFileName',return_value=('','')):tab.choose_manual()
    assert manager.imported is None
    tab.close()


def test_missing_audio_backend_is_nonmodal_and_separate_from_ready_model():
    application();manager=Manager();manager.ready=True;threads=[]
    def diagnostic():
        threads.append(threading.get_ident())
        return dict(backend_available=False,microphone_available=False,message='Системный компонент записи недоступен. Текстовый ввод работает.')
    tab=STTSettingsTab(manager,diagnostic_provider=diagnostic);wait(lambda:not tab.busy)
    assert 'Компоненты готовы' in tab.status_label.text()
    assert not tab.audio_warning.isHidden()
    assert 'Текстовый ввод' in tab.audio_warning.text()
    assert threads and threads[0]!=threading.get_ident()
    tab.close()


def test_missing_microphone_does_not_claim_model_is_missing():
    application();manager=Manager();manager.ready=True
    tab=STTSettingsTab(manager,diagnostic_provider=lambda:dict(backend_available=True,microphone_available=False,message='Подключите микрофон.'))
    wait(lambda:not tab.busy)
    assert 'Компоненты готовы' in tab.status_label.text()
    assert tab.audio_warning.text()=='Подключите микрофон.'
    tab.diagnostic_provider=lambda:dict(backend_available=True,microphone_available=True)
    tab.refresh();wait(lambda:not tab.busy)
    assert tab.audio_warning.isHidden()
    tab.close()


def test_audio_diagnostic_error_does_not_crash_settings():
    application();manager=Manager()
    def diagnostic():raise OSError('private loader detail')
    tab=STTSettingsTab(manager,diagnostic_provider=diagnostic);wait(lambda:not tab.busy)
    assert 'Текстовый ввод' in tab.audio_warning.text()
    assert 'private' not in tab.audio_warning.text()
    tab.close()
