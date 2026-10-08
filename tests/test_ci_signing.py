import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools import ci_signing


class SigningTests(unittest.TestCase):
    def test_tool_failure_does_not_disclose_arguments_or_password(self):
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([],1,'','private password')):
            with self.assertRaises(RuntimeError) as error:
                ci_signing.run(['security','import','-P','private password'])
            self.assertNotIn('private password',str(error.exception))

    def test_missing_credentials_does_not_touch_certificate_store(self):
        with patch.dict(os.environ,{},clear=True), patch.object(ci_signing,'run') as run, patch('sys.argv',['ci_signing.py']):
            ci_signing.main()
            run.assert_not_called()

    def test_windows_import_exports_settings_and_preserves_existing_certificate(self):
        with tempfile.TemporaryDirectory() as temp:
            sdk=Path(temp)/'Windows Kits/10/bin/10.0/x64'
            sdk.mkdir(parents=True);(sdk/'signtool.exe').write_bytes(b'test')
            env=Path(temp)/'github-env'
            values={'RUNNER_TEMP':temp,'GITHUB_ENV':str(env),'ProgramFiles(x86)':temp,
                    'WINDOWS_CERTIFICATE_BASE64':base64.b64encode(b'dummy-certificate').decode(),
                    'WINDOWS_CERTIFICATE_PASSWORD':'test-only'}
            with patch.dict(os.environ,values,clear=True),patch('sys.platform','win32'),patch('sys.argv',['ci_signing.py']),patch.object(ci_signing,'run',return_value=json.dumps({'thumbprint':'ABC123','imported':False})) as run:
                ci_signing.main()
                self.assertIn('WINDOWS_SIGN_THUMBPRINT=ABC123',env.read_text())
                self.assertFalse((Path(temp)/'orangenotes-signing/certificate.p12').exists())
                run.reset_mock()
                with patch('sys.argv',['ci_signing.py','--cleanup']):ci_signing.main()
                run.assert_not_called()
                self.assertFalse((Path(temp)/'orangenotes-signing').exists())
