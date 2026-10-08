import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools import package_deb, flatpak_bundle

ROOT=Path(__file__).resolve().parents[1]


class NativePackagingContracts(unittest.TestCase):
    def test_deb_uses_unprivileged_packager_no_install_hooks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle=root/'bundle';bundle.mkdir()
            (bundle/'OrangeNotes').write_bytes(b'fake');(bundle/'OrangeNotes.png').write_bytes(b'icon')
            def build(args,**kwargs):
                self.assertEqual(args[:3],['dpkg-deb','--build','--root-owner-group'])
                stage=Path(args[3])
                self.assertEqual({p.name for p in (stage/'DEBIAN').iterdir()},{'control'})
                self.assertFalse((stage/'etc').exists())
                self.assertIn('libglib2.0-bin',(stage/'DEBIAN/control').read_text())
            with patch.object(package_deb.subprocess,'run',side_effect=build):
                package_deb.package(bundle,root/'out.deb','0.1.0','amd64')

    def test_explicit_native_opt_in_requires_dbus(self):
        filename=ROOT/'tests/test_native_notification_integration.py'
        with patch.dict(os.environ,{'ORANGE_NATIVE_NOTIFICATION_TESTS':'1'}):
            spec=importlib.util.spec_from_file_location('native_opt_in',filename)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        case=module.NativeNotificationIntegration('test_linux_real_daemon_accepts_and_closes_notification')
        with patch('sys.platform','linux'),patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(AssertionError,'DBUS_SESSION_BUS_ADDRESS'):
                case.test_linux_real_daemon_accepts_and_closes_notification()

    def test_offline_flatpak_requires_real_commit_lock(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(flatpak_bundle,'run',return_value='x86_64'):
            with self.assertRaisesRegex(ValueError,'Missing runtime commit lock'):
                flatpak_bundle.main(['--lock',str(Path(temp)/'missing.json')])

    def test_flatpak_sources_are_local_and_manifest_identity_matches(self):
        manifest=json.loads(flatpak_bundle.MANIFEST.read_text())
        self.assertEqual(manifest['app-id'],'org.orangenotes.OrangeNotes')
        self.assertEqual(manifest['runtime-version'],'25.08')
        self.assertTrue(all(s['type'] in ('file','dir') and 'url' not in s for s in manifest['modules'][0]['sources']))
        self.assertFalse(any('filesystem=host' in a or 'share=network' in a for a in manifest['finish-args']))

    def test_locked_flatpak_build_disables_downloads(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle=root/'dist/OrangeNotes';bundle.mkdir(parents=True)
            (bundle/'OrangeNotes').write_bytes(b'fake executable')
            refs=['org.freedesktop.Platform/x86_64/25.08','org.freedesktop.Sdk/x86_64/25.08']
            lock=root/'runtime-lock.json';lock.write_text(json.dumps({'refs':refs,'commits':dict.fromkeys(refs,'a'*64)}))
            calls=[]
            def run(args):
                calls.append(args)
                if args==['flatpak','--default-arch']:return 'x86_64'
                if '--show-commit' in args:return 'a'*64
                return ''
            with patch.object(flatpak_bundle,'ROOT',root),patch.object(flatpak_bundle,'run',side_effect=run):
                flatpak_bundle.main(['--lock',str(lock)])
            command=next(c for c in calls if c[0]=='flatpak-builder')
            self.assertIn('--disable-download',command)
            self.assertIn('--disable-updates',command)
            self.assertFalse(any('install' in c or 'update' in c for c in calls))
            self.assertTrue((root/'dist/Flatpak-provenance.json').exists())
