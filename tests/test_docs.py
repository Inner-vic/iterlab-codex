"""Documentation links must resolve inside the actual distributable source."""
from pathlib import Path
import shutil
import unittest
import uuid

from scripts.check_docs import audit


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.parent = Path(__file__).resolve().parents[1] / '.evidence/tests'
        self.root = self.parent / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self):
        self.assertEqual(self.root.resolve().parent, self.parent.resolve())
        shutil.rmtree(self.root)

    def check(self, text, names):
        root = self.root
        (root / 'docs').mkdir()
        for name in [*names, 'docs/acceptance/private.md']:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture', encoding='utf-8')
        (root / 'docs/guide.md').write_text(text, encoding='utf-8')
        return audit(root, ['docs/guide.md', *names])

    def test_links_resolve_relative_to_document_including_encoded_paths(self):
        report = self.check('[root](../README.md) [file](<more%20info.txt#section>)',
                            ['README.md', 'docs/more info.txt'])
        self.assertEqual(report['issues'], [])
        self.assertEqual(report['local_links'], 2)

    def test_ignored_local_evidence_does_not_count_as_distributed(self):
        report = self.check('[proof](acceptance/private.md)', [])
        self.assertEqual(report['issues'][0]['target'], 'acceptance/private.md')
        self.assertEqual(report['issues'][0]['reason'], 'missing_or_not_distributed')

    def test_external_anchors_and_code_examples_are_not_file_links(self):
        report = self.check('[web](https://example.test/a) [section](#local)\n'
                            '```md\n[example](missing.md)\n```\n', [])
        self.assertEqual(report['local_links'], 0)
        self.assertEqual(report['issues'], [])

    def test_failure_line_is_preserved_after_code_block(self):
        report = self.check('```text\na\n```\n[missing](absent.md)', [])
        self.assertEqual(report['issues'][0]['line'], 4)

    def test_directory_links_only_resolve_if_content_is_distributed(self):
        report = self.check('[tree](../lab/) [absent](../private/)', ['lab/web.py'])
        self.assertEqual([item['target'] for item in report['issues']], ['../private/'])


if __name__ == '__main__':
    unittest.main()
