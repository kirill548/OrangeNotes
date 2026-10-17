"""Closing must stop producers before Qt starts deleting child widgets."""
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.window import Window

def test_accepted_close_stops_timers_and_ignores_late_tick(tmp_path):
    app=QApplication.instance() or QApplication([])
    store=Store(tmp_path/'notes.sqlite3')
    window=Window(store,background_managed=True)
    try:
        assert window.startup_tick.isActive()
        window.tray.hide();window.quitting=True
        window.close()
        assert not window.startup_tick.isActive()
        assert not window.clock.isActive()
        assert not window.debounce.isActive()
        with patch.object(window,'refresh_list',side_effect=AssertionError('late tick touched closing GUI')):
            window.tick()
    finally:
        window.tray.hide();window.quitting=True;window.close();window.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        app.processEvents();store.db.close()
