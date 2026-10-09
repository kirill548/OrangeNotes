"""Run the full suite with test names and periodic native-hang diagnostics."""
import faulthandler
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
class DiagnosticResult(unittest.TextTestResult):
    def startTest(self, test):
        super().startTest(test)
        # Any single test exceeding this bound fails CI with every thread stack.
        faulthandler.dump_traceback_later(90, exit=True)

    def stopTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        super().stopTest(test)


if __name__ == "__main__":
    faulthandler.enable()
    try:
        suite = unittest.defaultTestLoader.discover("tests")
        result = unittest.TextTestRunner(verbosity=2, resultclass=DiagnosticResult).run(suite)
    finally:
        faulthandler.cancel_dump_traceback_later()
    raise SystemExit(0 if result.wasSuccessful() else 1)
