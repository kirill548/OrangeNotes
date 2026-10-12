import tempfile
import unittest
from pathlib import Path

from tools.qwen_template import QWEN_CHAT_TEMPLATE, build_modelfile


class QwenTemplateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.gguf = Path(self.directory.name) / 'model with spaces.gguf'
        self.gguf.write_bytes(b'GGUF')

    def test_recipe_preserves_roles_and_assistant_generation_prefix(self):
        recipe = build_modelfile(self.gguf)
        self.assertIn('{{ .System }}<|im_end|>', recipe)
        self.assertIn('{{ range .Messages }}', recipe)
        self.assertIn('<|im_start|>{{ .Role }}\n{{ .Content }}<|im_end|>', recipe)
        self.assertIn('<|im_start|>assistant\n', recipe)
        self.assertNotIn('В ваших заметках', recipe)
        self.assertNotIn('SYSTEM ', recipe)

    def test_chat_and_generate_paths_both_include_system(self):
        self.assertEqual(QWEN_CHAT_TEMPLATE.count('{{ .System }}'), 2)
        self.assertIn('{{ if ne .Role "system" }}', QWEN_CHAT_TEMPLATE)
        self.assertIn('{{ .Prompt }}<|im_end|>', QWEN_CHAT_TEMPLATE)

    def test_stop_markers_and_bounded_context(self):
        recipe = build_modelfile(self.gguf, num_ctx=4096)
        for marker in ('<|im_start|>', '<|im_end|>', '<|endoftext|>'):
            self.assertIn(f'PARAMETER stop "{marker}"', recipe)
        self.assertIn('PARAMETER num_ctx 4096', recipe)
        for value in (0, 100000, True, '2048'):
            with self.assertRaises(ValueError):
                build_modelfile(self.gguf, num_ctx=value)

    def test_missing_or_non_gguf_source_rejected(self):
        with self.assertRaises(FileNotFoundError):
            build_modelfile(Path(self.directory.name) / 'missing.gguf')
        with self.assertRaises(ValueError):
            build_modelfile(Path(self.directory.name))

    def test_recipe_quotes_paths_and_does_not_modify_weights(self):
        self.assertTrue(build_modelfile(self.gguf).startswith(f'FROM "{self.gguf.resolve().as_posix()}"'))
        self.assertEqual(self.gguf.read_bytes(), b'GGUF')

    def test_cpu_recipe_is_explicit_without_changing_auto_default(self):
        self.assertIn('PARAMETER num_gpu 0\n', build_modelfile(self.gguf, device='cpu'))
        self.assertNotIn('PARAMETER num_gpu', build_modelfile(self.gguf))
        with self.assertRaises(ValueError):
            build_modelfile(self.gguf, device='unknown')
