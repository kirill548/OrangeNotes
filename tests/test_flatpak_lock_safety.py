import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import flatpak_bundle as tool


class FlatpakLockSafety(unittest.TestCase):
    refs=['org.freedesktop.Platform/x86_64/25.08','org.freedesktop.Sdk/x86_64/25.08']

    def test_rejects_empty_partial_and_non_string_locks(self):
        for value in ({},{'refs':self.refs,'commits':{}},{'refs':self.refs,'commits':dict.fromkeys(self.refs,12)}):
            with self.subTest(value=value), self.assertRaises(ValueError):tool.validate_lock(value,self.refs)

    def test_bootstrap_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(tool,'run',return_value='x86_64'):
            with self.assertRaisesRegex(ValueError,'explicit'):
                tool.main(['--prepare','--lock',str(Path(temp)/'lock.json')])

    def test_invalid_remote_commit_does_not_write_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            lock=Path(temp)/'lock.json'
            def run(args):
                if args==['flatpak','--default-arch']:return 'x86_64'
                if '--show-commit' in args:return 'untrusted truncated output'
                return ''
            with patch.object(tool,'run',side_effect=run),self.assertRaisesRegex(ValueError,'immutable commit'):
                tool.main(['--prepare','--bootstrap','--lock',str(lock)])
            self.assertFalse(lock.exists())

    def test_failed_atomic_replace_preserves_previous_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            lock=Path(temp)/'lock.json';lock.write_text('original')
            with patch.object(Path,'replace',side_effect=OSError('disk error')),self.assertRaises(OSError):
                tool.write_lock(lock,{'refs':self.refs,'commits':dict.fromkeys(self.refs,'a'*64)})
            self.assertEqual(lock.read_text(),'original')
            self.assertEqual([p.name for p in lock.parent.iterdir()],['lock.json'])

    def test_pinned_prepare_cannot_change_commits(self):
        with tempfile.TemporaryDirectory() as temp:
            lock=Path(temp)/'lock.json'
            previous={'refs':self.refs,'commits':dict.fromkeys(self.refs,'a'*64)};lock.write_text(json.dumps(previous))
            def run(args):
                if args==['flatpak','--default-arch']:return 'x86_64'
                if '--show-commit' in args:return 'b'*64
                return ''
            with patch.object(tool,'run',side_effect=run),self.assertRaisesRegex(ValueError,'mismatch'):
                tool.main(['--prepare','--lock',str(lock)])
            self.assertEqual(json.loads(lock.read_text()),previous)
