"""Cross-platform contracts; these do not prove physical macOS delivery."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tools import release_native
from app.services.portable_notifications import MacNotifications

class MacReleaseSafetyTests(unittest.TestCase):
    def test_rejected_notarization_never_staples(self):
        response=SimpleNamespace(stdout=json.dumps({'status':'Invalid'}))
        with patch.dict(os.environ,{'MAC_NOTARY_PROFILE':'ci','MAC_SIGN_IDENTITY':'Developer ID Application: Test'},clear=True), patch.object(release_native.subprocess,'run',return_value=response) as run:
            with self.assertRaisesRegex(RuntimeError,'not accepted'):
                release_native.mac_dmg(Path('/bundle.app'),Path('/out.dmg'))
        self.assertEqual(run.call_count,2)

    def test_accepted_notarization_staples_and_validates(self):
        response=SimpleNamespace(stdout=json.dumps({'status':'Accepted'}))
        with patch.dict(os.environ,{'MAC_NOTARY_PROFILE':'ci','MAC_SIGN_IDENTITY':'Developer ID Application: Test','MAC_NOTARY_KEYCHAIN':'/ephemeral.keychain'},clear=True), patch.object(release_native.subprocess,'run',return_value=response) as run:
            release_native.mac_dmg(Path('/bundle.app'),Path('/out.dmg'))
        self.assertIn('--output-format',run.call_args_list[1].args[0])
        self.assertIn('/ephemeral.keychain',run.call_args_list[1].args[0])
        self.assertEqual(run.call_args_list[-1].args[0][1:3],['stapler','validate'])

    def test_permission_status_retains_denied(self):
        transport=MacNotifications()
        transport.authorized=True
        settings=SimpleNamespace(authorizationStatus=lambda:1)
        transport.center=SimpleNamespace(getNotificationSettingsWithCompletionHandler_=lambda callback:callback(settings))
        self.assertEqual(transport.status()['setting'],'Denied')
        self.assertFalse(transport.authorized)

    def test_prompt_timeout_is_bounded_and_delivery_timeout_stays_short(self):
        transport=MacNotifications()
        with patch('app.services.portable_notifications.time.monotonic',side_effect=[0,121]):
            with self.assertRaises(OSError):transport._wait({},timeout=120)
        with patch('app.services.portable_notifications.time.monotonic',side_effect=[0,6]):
            with self.assertRaises(OSError):transport._wait({})

    def test_mac_harness_keeps_nonempty_report_after_cleanup(self):
        script=(Path(__file__).resolve().parents[1]/'tools/test_mac_bundle.sh').read_text()
        self.assertIn('mac-native-notifications.log',script)
        self.assertIn('[[ -s "$ORANGE_NATIVE_TEST_REPORT" ]]',script)
