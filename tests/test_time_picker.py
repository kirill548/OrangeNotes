"""Run standalone to exercise the wheel popup in native Qt."""
import sys
import tempfile
import unittest
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt,QTime,QDateTime,QTimer,QPoint,QPointF,QCoreApplication,QEvent,QObject
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication,QPushButton,QDateTimeEdit
from PySide6.QtTest import QTest
from app.database.store import Store
from app.ui.window import Window
from app.ui.time_picker import TimePopup


class PickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'notes.db')
        self.w=Window(self.store);self.w.clock.stop();self.w.show();self.w.new_note();self.w.edit_reminder();self.p=self.w.panel
        self.w.raise_();self.w.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.w,1000),'Picker test needs an active parent window before sending synthetic mouse events')
        self.errors=[]
    def tearDown(self):
        self.p.hide();self.w.quitting=True;self.w.tray.hide();self.w.close();self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.store.db.close();self.tmp.cleanup()
    def popup(self,edit,interaction,keyboard=False,child=False):
        # Switching reminder mode exposes the time widgets. Settle their layout
        # before choosing the actual line-edit text position for the click.
        self.app.processEvents()
        self.assertTrue(edit.isVisible())
        observed=[]
        def run(p):
            # Native Windows activation can lag behind Qt's timer under load.
            # Still fail if the popup never becomes active within the deadline.
            deadline=time.monotonic()+0.5
            while self.app.activePopupWidget() is not p and time.monotonic()<deadline:
                QTest.qWait(10)
            try:
                self.assertIs(self.app.activePopupWidget(),p)
                self.assertTrue(p.isVisible());interaction(p)
            except BaseException as error:self.errors.append(error)
            finally:
                if p and p.isVisible():p.reject()
        class PopupObserver(QObject):
            def eventFilter(observer,watched,event):
                if isinstance(watched,TimePopup) and event.type()==QEvent.Show and watched not in observed:
                    observed.append(watched)
                    # Begin interaction from actual Show, not a timer racing
                    # native activation/layout before the triggering click.
                    QTimer.singleShot(30,watched,lambda:run(watched))
                return False
        observer=PopupObserver()
        self.app.installEventFilter(observer)
        try:
            if keyboard:QTest.keyClick(edit,Qt.Key_Space)
            else:
                target=edit.lineEdit() if child else edit
                position=target.rect().center()
                if child:
                    position=QPoint(8,target.height()//2)
                    self.assertGreater(target.width(),16,'Text area must be laid out before clicking')
                QTest.mouseClick(target,Qt.LeftButton,pos=position)
                QTest.qWait(120)
            self.assertEqual(len(observed),1,'A single input must open one popup')
        finally:
            self.app.removeEventFilter(observer)
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        if self.errors:raise self.errors.pop(0)
    def apply(self,p):QTest.mouseClick(p.findChild(QPushButton,'timeApply'),Qt.LeftButton)
    def test_keyboard_boundaries_apply_and_persist(self):
        self.p.mode.setCurrentIndex(2);edit=self.p.times[0];edit.setTime(QTime(23,59))
        def run(p):
            QTest.keyClick(p.hours,Qt.Key_Down);QTest.keyClick(p.minutes,Qt.Key_Down)
            self.assertEqual(p.selectedTime(),QTime(0,0));self.apply(p)
        self.popup(edit,run);self.assertEqual(edit.time(),QTime(0,0))
        QTest.mouseClick(self.p.findChild(QPushButton,'primary'),Qt.LeftButton)
        self.assertEqual(self.store.reminder(self.w.current)['times'],['00:00'])
    def test_scroll_drag_row_click_cancel_and_cycles(self):
        self.p.mode.setCurrentIndex(2);edit=self.p.times[0];edit.setTime(QTime(0,0))
        def run(p):
            wheel=p.minutes
            event=QWheelEvent(QPointF(wheel.rect().center()),QPointF(wheel.mapToGlobal(wheel.rect().center())),QPoint(),QPoint(0,120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
            self.app.sendEvent(wheel,event);self.assertEqual(wheel.value,59)
            QTest.mousePress(p.hours,Qt.LeftButton,pos=QPoint(55,100));QTest.mouseMove(p.hours,QPoint(55,60));QTest.mouseRelease(p.hours,Qt.LeftButton,pos=QPoint(55,60))
            self.assertEqual(p.hours.value,1)
            QTest.mouseClick(p.minutes,Qt.LeftButton,pos=QPoint(55,140));self.assertEqual(p.minutes.value,0)
            QTest.keyClick(p,Qt.Key_Escape)
        self.popup(edit,run);self.assertEqual(edit.time(),QTime(0,0))
        for _ in range(30):self.popup(edit,lambda p:QTest.mouseClick(next(b for b in p.findChildren(QPushButton) if b.text()=='Отмена'),Qt.LeftButton))
        self.assertFalse(edit.findChildren(TimePopup));self.assertIsNone(self.app.activePopupWidget())
    def test_outside_cancel_and_popup_screenshot(self):
        self.p.mode.setCurrentIndex(2);edit=self.p.times[0];edit.setTime(QTime(9,30))
        def run(p):
            QTest.keyClick(p.hours,Qt.Key_Down)
            p.grab().save(str(Path(__file__).resolve().parents[3]/'work'/'time_picker.png'))
            QTest.mouseClick(p.windowHandle(),Qt.LeftButton,Qt.NoModifier,QPoint(-20,-20))
            self.assertFalse(p.isVisible(),'Outside click should cancel popup')
        self.popup(edit,run);self.assertEqual(edit.time(),QTime(9,30))
    def test_repeat_actual_child_text_click(self):
        self.p.mode.setCurrentIndex(2);edit=self.p.times[0];edit.setTime(QTime(9,30))
        def run(p):
            QTest.keyClick(p.hours,Qt.Key_Down);self.apply(p)
        self.popup(edit,run,child=True);self.assertEqual(edit.time(),QTime(10,30))
    def test_once_date_is_unchanged(self):
        before=QDateTime.currentDateTime().addDays(2);self.p.date.setDateTime(before);self.p.date.setCurrentSection(QDateTimeEdit.Section.HourSection)
        def run(p):
            QTest.keyClick(p.hours,Qt.Key_Down);self.apply(p)
        self.popup(self.p.date,run,keyboard=True)
        self.assertEqual(self.p.date.date(),before.date())
        self.assertEqual(self.p.date.time().hour(),(before.time().hour()+1)%24)
        QTest.mouseClick(self.p.findChild(QPushButton,'primary'),Qt.LeftButton)
        self.assertEqual(self.store.reminder(self.w.current)['once_at'][:10],before.date().toString('yyyy-MM-dd'))
    def test_once_mouse_time_section_and_original_release(self):
        edit=self.p.date;before=QDateTime.currentDateTime().addDays(3);edit.setDateTime(before)
        self.app.processEvents()
        line=edit.lineEdit()
        x=next(x for x in range(line.width()) if line.cursorPositionAt(QPoint(x,line.height()//2))==12)
        position=line.mapTo(edit,QPoint(x,line.height()//2))
        def run():
            p=self.app.activePopupWidget()
            try:
                self.assertIsInstance(p,TimePopup)
                QTest.mouseRelease(line,Qt.LeftButton,pos=QPoint(x,line.height()//2))
                self.app.processEvents();self.assertTrue(p.isVisible())
                QTest.keyClick(p.minutes,Qt.Key_Down);self.apply(p)
            except BaseException as error:self.errors.append(error)
            finally:
                if p and p.isVisible():p.reject()
        QTimer.singleShot(30,run)
        QTest.mousePress(line,Qt.LeftButton,pos=QPoint(x,line.height()//2))
        QTest.qWait(120)
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        if self.errors:raise self.errors.pop(0)
        self.assertEqual(edit.date(),before.date())
        self.assertEqual(edit.time().minute(),(before.time().minute()+1)%60)

if __name__=='__main__':unittest.main(verbosity=2)
