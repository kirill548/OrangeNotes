"""Isolated PyInstaller entry point for on-device notification tests."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest


def main():
    os.environ['ORANGE_NATIVE_NOTIFICATION_TESTS']='1'
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
    filename=root/'native_tests/test_native_notification_integration.py'
    spec=importlib.util.spec_from_file_location('native_notifications',filename)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    destination=Path(os.environ['ORANGE_NATIVE_TEST_REPORT'])
    with destination.open('w',encoding='utf-8') as report:
        result=unittest.TextTestRunner(stream=report,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
        return 0 if result.wasSuccessful() and result.testsRun==2 and len(result.skipped)==1 else 1


if __name__=='__main__':sys.exit(main())
