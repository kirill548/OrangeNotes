import hashlib
import json
import os
import platform
from pathlib import Path
import tempfile
import threading
import unittest
from app.services.bundled_ai import validate_pack, install_complete_seed, find_complete_seed, _arch

class BundledAITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.seed=self.root/'bundled_ai';self.seed.mkdir()
        self.exe='runtime/ollama/ollama.exe' if os.name=='nt' else 'runtime/ollama/ollama'
        weights=b'fixture weights';digest=hashlib.sha256(weights).hexdigest()
        payload={self.exe:b'fixture executable', 'models/blobs/sha256-'+digest:weights,
                 'models/manifests/registry.ollama.ai/library/qwen3/4b':json.dumps({'config':{'digest':'sha256:'+digest},'layers':[]}).encode()}
        for name,data in payload.items():
            target=self.seed/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);target.chmod(0o755)
        import sys
        self.manifest={'schema_version':1,'engine':'ollama','platform':sys.platform,'architecture':_arch(platform.machine()),'model':'qwen3:4b','source':'synthetic fixture','engine_version':'test','license':'test','files':{name:{'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in payload.items()}}
        self.save()
    def save(self):(self.seed/'bundle_manifest.json').write_text(json.dumps(self.manifest))
    def test_verified_seed_and_install(self):
        self.assertEqual(find_complete_seed(self.root)[0],self.seed)
        config=install_complete_seed(self.seed,self.root/'data')
        self.assertTrue(config['managed_pack']);self.assertTrue((Path(config['runtime_root'])/self.exe).exists())
        from app.services.ai_pack_storage import AIPackStorage
        self.assertEqual(AIPackStorage(Path(config['runtime_root'])/'models').scan()[0].name,'qwen3:4b')
    def test_corrupt_weights_refused(self):
        next((self.seed/'models/blobs').iterdir()).write_bytes(b'corruption')
        with self.assertRaises(ValueError):validate_pack(self.seed)
    def test_wrong_platform(self):
        self.manifest['platform']='imaginary';self.save()
        with self.assertRaises(ValueError):validate_pack(self.seed)
    def test_verified_runtime_profile_is_preserved_in_installed_config(self):
        self.manifest['runtime_profile']='vulkan-safe';self.save()
        config=install_complete_seed(self.seed,self.root/'data')
        self.assertEqual(config['runtime_profile'],'vulkan-safe')
    def test_unknown_runtime_profile_is_rejected_before_install(self):
        self.manifest['runtime_profile']='arbitrary-env';self.save()
        with self.assertRaises(ValueError):install_complete_seed(self.seed,self.root/'data')
        self.assertFalse((self.root/'data/ai_packs').exists())
    def test_unlisted_file(self):
        (self.seed/'secret').write_text('private')
        with self.assertRaises(ValueError):validate_pack(self.seed)
    def test_external_store_preserved(self):
        data=self.root/'data';models=data/'ai_packs/models';models.mkdir(parents=True);(models/'user').write_text('keep')
        with self.assertRaises(ValueError):install_complete_seed(self.seed,data)
        self.assertEqual((models/'user').read_text(),'keep')
    def test_cancel_cleans_temporary_files(self):
        cancel=threading.Event();cancel.set();data=self.root/'data'
        with self.assertRaises(InterruptedError):install_complete_seed(self.seed,data,cancel)
        self.assertFalse((data/'ai_packs').exists());self.assertEqual(list(data.glob('.complete-*')),[])
    def test_path_traversal_refused(self):
        self.manifest['files']['../escape']={'size':1,'sha256':'a'*64};self.save()
        with self.assertRaises(ValueError):validate_pack(self.seed)

    def test_internal_resources_discovery(self):
        internal=self.root/'_internal';internal.mkdir()
        self.seed.rename(internal/'bundled_ai')
        self.assertEqual(find_complete_seed(self.root)[0],internal/'bundled_ai')
    def test_nested_unlisted_manifest_rejected(self):
        (self.seed/'runtime/bundle_manifest.json').write_text('{}')
        with self.assertRaises(ValueError):validate_pack(self.seed)
    def test_ambiguous_seed_rejected(self):
        (self.root/'_internal/bundled_ai').mkdir(parents=True)
        with self.assertRaises(ValueError):find_complete_seed(self.root)

    def test_private_data_even_if_listed_rejected(self):
        payload=b'private'
        (self.seed/'notes.db').write_bytes(payload)
        self.manifest['files']['notes.db']={'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()};self.save()
        with self.assertRaises(ValueError):validate_pack(self.seed)

    def test_initialized_marker_only_store_can_install(self):
        from app.services.ai_pack_storage import AIPackStorage
        data=self.root/'data';destination=data/'ai_packs'
        AIPackStorage(destination).initialize();AIPackStorage(destination/'models').initialize()
        result=install_complete_seed(self.seed,data)
        self.assertTrue((destination/self.exe).exists());self.assertTrue(result['managed_pack'])
    def test_wrong_owner_marker_preserved(self):
        from app.services.ai_pack_storage import AIPackStorage
        data=self.root/'data';destination=data/'ai_packs';destination.mkdir(parents=True)
        (destination/AIPackStorage.MARKER).write_text('wrong owner')
        with self.assertRaises(ValueError):install_complete_seed(self.seed,data)
        self.assertEqual((destination/AIPackStorage.MARKER).read_text(),'wrong owner')

    def test_retry_after_install_before_config_returns_same_store(self):
        data=self.root/'data';first=install_complete_seed(self.seed,data)
        model=next((data/'ai_packs/models/blobs').iterdir());stamp=model.stat().st_mtime_ns
        second=install_complete_seed(self.seed,data)
        self.assertEqual(first,second);self.assertEqual(model.stat().st_mtime_ns,stamp)
    def test_resume_changed_model_refused_without_overwrite(self):
        data=self.root/'data';install_complete_seed(self.seed,data)
        model=next((data/'ai_packs/models/blobs').iterdir());model.write_bytes(b'user changed weights')
        with self.assertRaises(ValueError):install_complete_seed(self.seed,data)
        self.assertEqual(model.read_bytes(),b'user changed weights')
    def test_resume_fingerprint_mismatch_refused(self):
        data=self.root/'data';install_complete_seed(self.seed,data)
        self.manifest['engine_version']='different';self.save()
        with self.assertRaises(ValueError):install_complete_seed(self.seed,data)

    def test_cancel_validation_before_manifest_read(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError):validate_pack(self.root/'missing',cancel_event=cancel)
    def test_cancel_validation_during_hash(self):
        class CancelOnHash:
            def __init__(self):self.checks=0
            def is_set(self):
                self.checks+=1
                return self.checks>=2
        cancel=CancelOnHash()
        with self.assertRaises(InterruptedError):validate_pack(self.seed,cancel_event=cancel)
        self.assertEqual(cancel.checks,2)
