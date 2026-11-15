import uuid
from pathlib import Path
import unittest
from lab.assets import StaticBundle


class AssetBundleTests(unittest.TestCase):
    def test_versioned_compression_is_lossless_and_never_includes_runtime_html_token(self):
        import gzip
        bundle=StaticBundle(Path(__file__).resolve().parents[1]/'static')
        path='/assets/'+bundle.revision+'/vendor/exploration-map.js'
        compressed=bundle.gzip(path)
        self.assertEqual(gzip.decompress(compressed),bundle.read(path))
        self.assertLess(len(compressed),len(bundle.read(path)))
        self.assertIsNone(bundle.gzip('/assets/old/app.js'))
        self.assertIsNone(bundle.gzip('/index.html'))

    def test_favicons_are_local_and_versioned(self):
        import mimetypes
        bundle=StaticBundle(Path(__file__).resolve().parents[1]/'static')
        html=bundle.html('fixture-token')
        for name in ('favicon.svg','favicon.ico'):
            self.assertIsNotNone(bundle.read('/'+name))
            self.assertEqual(bundle.read('/'+name),bundle.read('/assets/'+bundle.revision+'/'+name))
            self.assertIn('/assets/'+bundle.revision+'/'+name,html)
        self.assertEqual(mimetypes.guess_type('favicon.svg')[0],'image/svg+xml')
        self.assertIn(mimetypes.guess_type('favicon.ico')[0],{'image/vnd.microsoft.icon','image/x-icon'})
        self.assertTrue(bundle.read('/favicon.ico').startswith(b'\x00\x00\x01\x00'))

    def test_running_host_keeps_original_assets_when_source_is_edited(self):
        root=Path(__file__).resolve().parents[1]/'.evidence/tests'/uuid.uuid4().hex
        root.mkdir(parents=True)
        (root/'index.html').write_text('<meta content="__ASSET_REVISION__"><script src="/app.js"></script>__CLIENT_TOKEN__',encoding='utf-8')
        script=root/'app.js';script.write_bytes(b'original')
        running=StaticBundle(root);script.write_bytes(b'next release')
        self.assertEqual(running.read('/app.js'),b'original')
        self.assertEqual(running.read('/assets/'+running.revision+'/app.js'),b'original')
        new=StaticBundle(root);self.assertNotEqual(new.revision,running.revision)
        self.assertIsNone(new.read('/assets/'+running.revision+'/app.js'))
        self.assertIsNone(running.read('/../app.js'))
        html=running.html('fixture-token')
        self.assertIn('/assets/'+running.revision+'/app.js',html)
        self.assertNotIn('__CLIENT_TOKEN__',html);self.assertNotIn('__ASSET_REVISION__',html)
