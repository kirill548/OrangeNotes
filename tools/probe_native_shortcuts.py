import tempfile,sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.window import Window
a=QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as t:
 s=Store(Path(t)/'probe.sqlite3');w=Window(s,background_managed=True);w.clock.stop();w.show();w.activateWindow();QTest.qWaitForWindowActive(w,3000);w.new_note();a.processEvents()
 print('platform',a.platformName(),'Paste',QKeySequence(QKeySequence.StandardKey.Paste).toString(),'focus',a.focusWidget().objectName())
 for seq in [QKeySequence(QKeySequence.StandardKey.Paste),QKeySequence('Ctrl+V'),QKeySequence('Meta+V')]:
  w.body.clear();w.body.setFocus();QTest.qWait(30);a.clipboard().setText('PROBE');QTest.keySequence(w.body,seq);QTest.qWait(100);print('paste',seq.toString(),repr(w.body.toPlainText()),'focus',a.focusWidget() is w.body)
 for seq in ['Ctrl+F','Meta+F']:
  w.body.setFocus();QTest.qWait(30);QTest.keySequence(w.body,QKeySequence(seq));QTest.qWait(100);print('find',seq,a.focusWidget() is w.search)
 w.quitting=True;w.close();w.tray.hide();s.db.close()
