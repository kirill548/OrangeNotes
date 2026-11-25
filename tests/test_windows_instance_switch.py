import os
from pathlib import Path
import sys

import pytest
from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication

from app import main


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_other_build_waits_until_original_releases_lock(app, tmp_path):
    lock_path = str(tmp_path / 'app.lock')
    old = QLockFile(lock_path)
    new = QLockFile(lock_path)
    assert old.tryLock(0)
    assert not new.tryLock(0)
    QTimer.singleShot(50, old.unlock)

    assert main.wait_for_running_build(new, 'unused-socket', Path(sys.executable))
    assert new.isLocked()
    new.unlock()


def test_cancel_does_not_unlock_old_build(app, tmp_path):
    lock_path = str(tmp_path / 'app.lock')
    old = QLockFile(lock_path)
    new = QLockFile(lock_path)
    assert old.tryLock(0)

    QTimer.singleShot(50, lambda: app.activeModalWidget().reject())
    assert not main.wait_for_running_build(new, 'unused-socket', Path(sys.executable))
    assert old.isLocked()
    assert not new.isLocked()
    old.unlock()


@pytest.mark.skipif(sys.platform != 'win32', reason='Win32 process image lookup')
def test_windows_process_image_matches_running_executable():
    from app.utils.windows_instance import executable_path

    assert executable_path(os.getpid()) == Path(sys.executable).resolve()
    assert executable_path(0) is None


@pytest.mark.skipif(sys.platform != 'win32', reason='Win32 process image lookup')
def test_running_build_is_detected_from_qt_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'FROZEN', True)
    lock_path = str(tmp_path / 'app.lock')
    owner = QLockFile(lock_path)
    assert owner.tryLock(0)
    try:
        assert main.running_windows_build(QLockFile(lock_path)) == Path(sys.executable).resolve()
    finally:
        owner.unlock()
