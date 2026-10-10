import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import threading
import time
import unittest
from PySide6.QtWidgets import QApplication
from app.widgets.ai_pack_settings import AIPackSettingsTab,AIPackSettingsDialog
from app.ui.companion import validate_config

class Manager:
    available_models=[{'name':'test:small','download_bytes':1024,'ram_bytes':2048}]
    def __init__(self):self.calls=[];self.release=threading.Event();self.block=False
    def scan(self):
        self.calls.append(threading.get_ident())
        return [{'name':'test:small','size_bytes':1024,'reclaimable_bytes':512,'active':False,'managed':True}]
    def download(self,model,cancel_event,progress):
        self.calls.append(threading.get_ident())
        while self.block and not cancel_event.is_set():time.sleep(.005)
    def switch_active_model(self,model,cancel_event):
        self.calls.append(threading.get_ident());return {'model':model,'managed_pack':True}

class PackUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def wait(self,predicate):
        deadline=time.monotonic()+3
        while not predicate() and time.monotonic()<deadline:self.app.processEvents();time.sleep(.005)
        self.assertTrue(predicate())
    def test_scan_and_switch_run_off_main_thread(self):
        manager=Manager();tab=AIPackSettingsTab(manager);self.wait(lambda:not tab.busy)
        self.assertEqual(tab.table.rowCount(),1)
        self.assertIn('ОЗУ',tab.catalogue.itemText(0))
        seen=[];tab.configured.connect(seen.append);tab.table.selectRow(0);tab._switch()
        self.wait(lambda:not tab.busy)
        self.assertEqual(seen,[{'model':'test:small','managed_pack':True}])
        self.assertTrue(all(identity!=threading.get_ident() for identity in manager.calls))
        tab.deleteLater()
    def test_close_cancels_and_retains_worker(self):
        manager=Manager();dialog=AIPackSettingsDialog();tab=AIPackSettingsTab(manager,dialog);dialog.pack_tab=tab
        tab.idle.connect(dialog.finish_pending_close);self.wait(lambda:not tab.busy)
        manager.block=True;dialog.show();tab._start('download','test:small');worker=tab._worker
        dialog.close();self.assertIs(tab._worker,worker);self.assertTrue(worker.cancel_event.is_set())
        self.wait(lambda:not tab.busy);self.assertFalse(dialog.isVisible());dialog.deleteLater()
    def test_managed_flag_requires_bool(self):
        self.assertFalse(validate_config({})['managed_pack'])
        with self.assertRaises(ValueError):validate_config({'managed_pack':1})
    def test_pull_progress_percent_and_status_are_visible(self):
        tab=AIPackSettingsTab(Manager());self.wait(lambda:not tab.busy)
        tab._progress({'model':'test:small','percent':43,'status':'downloading sha256'})
        self.assertEqual(tab.progress_bar.maximum(),100)
        self.assertEqual(tab.progress_bar.value(),43)
        self.assertEqual(tab.status.text(),'downloading sha256')
        tab._progress({'percent':150,'status':'verifying digest'})
        self.assertEqual(tab.progress_bar.value(),100)
        self.assertEqual(tab.status.text(),'verifying digest')
        tab.deleteLater()

if __name__=='__main__':unittest.main()
