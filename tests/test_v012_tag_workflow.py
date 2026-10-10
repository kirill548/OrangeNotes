"""Release tag orchestration must use trusted immutable main source."""
from pathlib import Path
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/tag-v012.yml'


class V012TagWorkflowTests(unittest.TestCase):
    def test_only_release_notes_on_main_trigger_tag(self):
        source = WORKFLOW.read_text(encoding='utf-8')
        self.assertIn('branches: [main]', source)
        self.assertIn('paths: [release_notes.md]', source)
        self.assertIn("github.ref == 'refs/heads/main' && github.event_name == 'push'", source)
        self.assertIn('ref: ${{ github.sha }}', source)
        self.assertIn('$(git rev-parse origin/main)', source)

    def test_existing_tag_must_be_annotated_and_same_commit(self):
        source = WORKFLOW.read_text(encoding='utf-8')
        self.assertIn('git cat-file -t refs/tags/v0.1.2', source)
        self.assertIn("refs/tags/v0.1.2^{commit}", source)
        self.assertIn("git tag -a v0.1.2 \"$RELEASE_SHA\" -m 'Release v0.1.2'", source)
        self.assertIn('git push origin refs/tags/v0.1.2', source)
        self.assertNotIn('--force', source)
        self.assertNotIn('git tag -d', source)

    def test_token_tag_push_followed_by_explicit_typed_dispatch(self):
        source = WORKFLOW.read_text(encoding='utf-8')
        self.assertIn('contents: write', source)
        self.assertIn('actions: write', source)
        self.assertIn('actions/workflows/build.yml/dispatches', source)
        self.assertIn('"ref":"v0.1.2"', source)
        self.assertIn('"flatpak":true', source)
        self.assertIn('"native_macos_notifications":false', source)
        self.assertIn('--input "$RUNNER_TEMP/native-build-dispatch.json"', source)
