import unittest

from app.services.runtime_profiles import runtime_environment


class RuntimeProfileTests(unittest.TestCase):
    def test_safe_profile_changes_child_environment_only(self):
        original = {'OLLAMA_HOST': '127.0.0.1:11573', 'OLLAMA_VULKAN': 'false'}
        result = runtime_environment(original, 'vulkan-safe')
        self.assertEqual(result['OLLAMA_VULKAN'], 'true')
        self.assertEqual(result['OLLAMA_FLASH_ATTENTION'], 'false')
        self.assertEqual(result['OLLAMA_NUM_PARALLEL'], '1')
        self.assertEqual(result['OLLAMA_HOST'], original['OLLAMA_HOST'])
        self.assertEqual(original['OLLAMA_VULKAN'], 'false')

    def test_default_keeps_existing_environment(self):
        original = {'OLLAMA_FLASH_ATTENTION': 'true'}
        self.assertEqual(runtime_environment(original), original)
        self.assertIsNot(runtime_environment(original), original)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            runtime_environment({}, 'arbitrary-environment')
