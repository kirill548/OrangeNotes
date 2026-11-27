import ctypes
from ctypes import wintypes
import hashlib
import io
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QMenu, QPushButton, QWidget, QVBoxLayout

from app.services.desktop_updates import INSTALLER, REPOSITORY, download_update, select_release
from app.services.windows_update import END_SESSION, END_SESSION_CLOSE_APP, QUERY_END_SESSION, handle_setup_message
from app.ui.desktop_updates import DesktopUpdates
from app.ui.window import Window


def metadata(payload=b'test installer'):
    tag = 'v0.2.10'
    return {
        'draft': False, 'prerelease': False, 'tag_name': tag,
        'assets': [{
            'name': INSTALLER, 'state': 'uploaded', 'size': len(payload),
            'browser_download_url': f'{REPOSITORY}/releases/download/{tag}/{INSTALLER}',
            'digest': 'sha256:' + hashlib.sha256(payload).hexdigest(),
        }],
    }


@pytest.mark.parametrize('changes', [
    {'draft': True}, {'prerelease': True}, {'tag_name': 'v0.2.9'},
    {'tag_name': 'v0.2.8'}, {'tag_name': 'v0.2.20-rc.1'}, {'tag_name': 'v0.3.0/other'},
])
def test_release_rejects_unstable_or_nonnewer_versions(changes):
    assert select_release(metadata() | changes, '0.2.9') is None


@pytest.mark.parametrize('changes', [
    {'digest': None}, {'browser_download_url': 'https://example.org/other.exe'},
    {'size': 0}, {'state': 'new'},
])
def test_unverified_installer_only_offers_release_page(changes):
    data = metadata()
    data['assets'][0].update(changes)
    release = select_release(data, '0.2.9')
    assert release.page == REPOSITORY + '/releases/tag/v0.2.10'
    assert release.installer is None


def test_download_verifies_size_and_digest(tmp_path, monkeypatch):
    monkeypatch.setattr('app.services.desktop_updates.tempfile.tempdir', str(tmp_path))
    payload = b'test installer'
    release = select_release(metadata(payload), '0.2.9')
    target = download_update(release, threading.Event(), lambda url: io.BytesIO(payload))
    assert target.read_bytes() == payload


@pytest.mark.parametrize('payload,cancel', [(b'tampered bytes', False), (b'short', False),
                                            (b'long installer payload', False), (b'test installer', True)])
def test_failed_download_cleans_up(tmp_path, monkeypatch, payload, cancel):
    monkeypatch.setattr('app.services.desktop_updates.tempfile.tempdir', str(tmp_path))
    release = select_release(metadata(), '0.2.9')
    cancelled = threading.Event()
    if cancel:
        cancelled.set()
    with pytest.raises(ValueError):
        download_update(release, cancelled, lambda url: io.BytesIO(payload))
    assert list(tmp_path.iterdir()) == []


def test_update_protects_companion_drafts_and_checks_save():
    companion = SimpleNamespace(busy=False, has_unsubmitted_input=Mock(return_value=True))
    window = SimpleNamespace(_companion_dialog=companion, panel=None, save=Mock(return_value=True))
    assert not Window.prepare_for_update(window)
    window.save.assert_not_called()
    companion.has_unsubmitted_input.return_value = False
    window.panel = SimpleNamespace(isVisible=Mock(return_value=True), has_changes=Mock(return_value=True))
    assert not Window.prepare_for_update(window)
    window.panel.isVisible.return_value = False
    window.save.return_value = False
    assert not Window.prepare_for_update(window)
    window.save.return_value = True
    assert Window.prepare_for_update(window)
    window.save.assert_called_with(refresh=False, report_error=False)


def test_restart_manager_shutdown_only_exits_after_confirmation():
    window = SimpleNamespace(prepare_for_update=Mock(return_value=False), quit=Mock())
    message = wintypes.MSG()
    message.message = QUERY_END_SESSION
    address = ctypes.addressof(message)
    assert handle_setup_message(window, b'windows_generic_MSG', address) is None
    message.lParam = END_SESSION_CLOSE_APP
    assert handle_setup_message(window, b'windows_generic_MSG', address) == 0
    window.prepare_for_update.return_value = True
    assert handle_setup_message(window, b'windows_generic_MSG', address) == 1
    message.message = END_SESSION
    assert handle_setup_message(window, b'windows_generic_MSG', address) == 0
    window.quit.assert_not_called()
    message.wParam = 1
    handle_setup_message(window, b'windows_generic_MSG', address)
    window.quit.assert_called_once()


def test_background_result_announces_update_without_installing(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.status_actions = QVBoxLayout(window)
    window.app_menu_button = QPushButton(window)
    menu = QMenu(window.app_menu_button)
    menu.status_action = menu.addAction('Состояние напоминаний')
    window.app_menu_button.setMenu(menu)
    monkeypatch.setattr('app.ui.desktop_updates.install_scope', lambda _: 'machine')
    start = Mock()
    monkeypatch.setattr('app.ui.desktop_updates.launch_installer', start)
    updates = DesktopUpdates(window, 'OrangeNotes.exe')
    try:
        release = select_release(metadata(), '0.2.9')
        updates.pending = (True, False)
        updates.results.put((True, release))
        updates.receive()
        assert updates.button.text() == 'Обновление 0.2.10'
        assert not updates.button.isHidden()
        assert updates.action in menu.actions()
        start.assert_not_called()
    finally:
        window.deleteLater()
        app.processEvents()
