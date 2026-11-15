import unittest, struct, re, json
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
NS='{http://www.w3.org/2000/svg}'
class BrandingTests(unittest.TestCase):
    def test_master_shapes_match_favicon_and_runtime_module(self):
        mark=ET.parse(ROOT/'static/brand-mark.svg').getroot()
        icon=ET.parse(ROOT/'static/favicon.svg').getroot()
        module=(ROOT/'static/brand.js').read_text(encoding='utf-8')
        self.assertEqual(mark.attrib['viewBox'],'0 0 72 64')
        for element in mark:
            actual=icon.find('.//'+element.tag)
            self.assertIsNotNone(actual)
            self.assertEqual(dict(element.attrib),dict(actual.attrib))
        exported=json.loads(re.search(r'export const brandMark=(.*);',module)[1])
        self.assertEqual(exported,(ROOT/'static/brand-mark.svg').read_text().strip())
    def test_wordmark_is_fixed_local_vector(self):
        word=ET.parse(ROOT/'static/brand-wordmark.svg').getroot()
        self.assertEqual(word.attrib['aria-label'],'IterLab')
        self.assertEqual(len(word.findall(NS+'path')),7)
        self.assertIsNone(word.find('.//'+NS+'text'))
        module=(ROOT/'static/brand.js').read_text()
        exported=json.loads(re.search(r'export const brandWordmark=(.*);',module)[1])
        self.assertEqual(exported,(ROOT/'static/brand-wordmark.svg').read_text().strip())
    def test_brand_vectors_are_safe_and_static(self):
        for name in ['brand-mark.svg','brand-wordmark.svg','favicon.svg']:
            root=ET.parse(ROOT/'static'/name).getroot()
            for element in root.iter():
                self.assertIn(element.tag.split('}')[-1],['svg','rect','ellipse','path'])
                for attr in element.attrib:self.assertFalse(attr.lower().startswith('on') or 'href' in attr)
        module=(ROOT/'static/brand.js').read_text()
        self.assertNotRegex(module,r'\b(?:fetch|localStorage|sessionStorage|location|setInterval)\b')
        self.assertNotIn('data-brand=b',module)
    def test_brand_mounts_and_both_welcome_surfaces_are_consistent(self):
        html=(ROOT/'static/index.html').read_text()
        self.assertEqual(html.count('data-brand-mark'),2)
        # The compact home hero uses the mark and Chinese headline; the full
        # bilingual wordmark lockup belongs to the empty chat, not both views.
        self.assertEqual(html.count('data-brand-wordmark'),1)
        self.assertIn('让好奇，长出新知。',html)
        for value in ['让好奇，长出新知。','Let curiosity unfold.']:
            self.assertIn(value,(ROOT/'static/brand.js').read_text())
        app=(ROOT/'static/app.js').read_text()
        self.assertIn('installBrand(document)',app)
        conversation=(ROOT/'static/conversation.js').read_text()
        self.assertIn("setHTML(row,brandWelcome)",conversation)
        self.assertIn("setText(entry.title,session.project_title)",conversation)
        self.assertIn("setText(entry.workspace,session.workspace)",conversation)
    def test_no_remote_fonts_or_preview_controls(self):
        css=(ROOT/'static/branding.css').read_text()
        self.assertNotIn('@import',css);self.assertNotIn('url(',css)
        self.assertNotIn('data-brand=b',css)
        self.assertIn('SimSun',css)
        self.assertIn('.sidebar-collapsed .brand-name{display:none}',css)
    def test_ico_directory_sizes(self):
        data=(ROOT/'static/favicon.ico').read_bytes()
        reserved,kind,count=struct.unpack_from('<HHH',data)
        self.assertEqual((reserved,kind,count),(0,1,4))
        sizes={(data[6+16*i] or 256,data[7+16*i] or 256) for i in range(count)}
        self.assertEqual(sizes,{(16,16),(32,32),(48,48),(64,64)})
if __name__=='__main__':unittest.main()
