"""Contracts for the evidence collected by each native CI runner."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cross_platform_qa', ROOT / 'tools/run_cross_platform_qa.py')
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


class CrossPlatformQATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(qa, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.out = self.root / 'work/cross-platform-qa'

    def test_counts_include_nested_suites_failure_error_and_skip(self):
        report = self.root / 'report.xml'
        report.write_text('<testsuites><testsuite><testcase/><testcase><failure/></testcase>'
                          '<testcase><error/></testcase><testcase><skipped/></testcase>'
                          '</testsuite><testsuite><testcase/></testsuite></testsuites>')
        self.assertEqual(qa.counts(report), dict(passed=2, failed=2, skipped=1))

    def test_environment_records_actual_sqlite_and_unicode_fts(self):
        env = qa.environment()
        for key in ('os', 'architecture', 'python', 'sqlite'):
            self.assertIsInstance(env[key], str)
            self.assertTrue(env[key])
        self.assertEqual(env['sqlite'], qa.sqlite3.sqlite_version)
        self.assertIsInstance(env['fts5_unicode61'], bool)

    def test_failed_pytest_preserves_json_counts_and_log(self):
        def run(command, **kwargs):
            self.assertIn('tests', command)
            self.assertEqual(kwargs['cwd'], self.root)
            (self.out / 'pytest.xml').write_text('<testsuite><testcase><failure/></testcase></testsuite>')
            return subprocess.CompletedProcess(command, 1, 'failure details')
        with patch.object(qa.subprocess, 'run', side_effect=run):
            self.assertEqual(qa.main(), 1)
        summary = json.loads((self.out / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['status'], 'failed')
        self.assertEqual(summary['counts'], dict(passed=0, failed=1, skipped=0))
        self.assertIn('environment', summary)
        self.assertEqual((self.out / 'pytest.log').read_text(), 'failure details')

    def test_timeout_preserves_partial_bytes_and_removes_stale_report(self):
        self.out.mkdir(parents=True)
        (self.out / 'pytest.xml').write_text('<testsuite><testcase/></testsuite>')
        with patch.object(qa.subprocess, 'run', side_effect=subprocess.TimeoutExpired('pytest', 600, output=b'partial output')):
            self.assertEqual(qa.main(), 124)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'timeout')
        self.assertIsNone(summary['counts'])
        self.assertFalse((self.out / 'pytest.xml').exists())
        self.assertIn('partial output', (self.out / 'pytest.log').read_text())

    def test_launch_error_still_writes_summary(self):
        with patch.object(qa.subprocess, 'run', side_effect=OSError('launch failed')):
            self.assertEqual(qa.main(), 1)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'error')
        self.assertIn('launch failed', summary['error'])

    def test_workflow_runs_each_architecture_and_always_uploads_evidence(self):
        workflow = (ROOT / '.github/workflows/build.yml').read_text()
        for runner in ('windows-2022', 'ubuntu-22.04', 'macos-15-intel', 'macos-14'):
            self.assertIn('os: ' + runner, workflow)
        self.assertIn('fail-fast: false', workflow)
        self.assertEqual(workflow.count('tools/run_cross_platform_qa.py'), 3)
        self.assertIn('if: always()\n        with:\n          name: QA-${{ matrix.name }}', workflow)
        self.assertIn('path: work/cross-platform-qa/', workflow)
