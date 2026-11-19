# -*- coding: utf-8 -*-
"""The product follows the 不晚 visual spec (design/buwan-ds): tokens come from the spec, never from copies."""
import re
import tempfile
import unittest
from pathlib import Path
from backend.api import Application, BUWAN_FILES, BUWAN_ROOT
from backend.config import Config

ROOT = Path(__file__).resolve().parents[1]
STYLE = (ROOT / 'frontend/style.css').read_text(encoding='utf-8')
TOKENS = (BUWAN_ROOT / 'styles/tokens.css').read_text(encoding='utf-8')


class VisualSpecTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();root = Path(self.temp.name)
        self.app = Application(Config(db=root / 'v.sqlite3', log_dir=root / 'logs', origin='http://test.local'))
    def tearDown(self):
        for h in list(self.app.logger.handlers):h.close();self.app.logger.removeHandler(h)
        self.temp.cleanup()
    def get(self, path):return self.app.handle('GET', path, {'Host': 'test.local'}, b'')

    def test_spec_files_are_served_as_they_are(self):
        for path in BUWAN_FILES:
            r = self.get(path)
            self.assertEqual(r.status, 200, path)
            self.assertEqual(r.body, (BUWAN_ROOT / path[len('/buwan/'):]).read_bytes())
        for path in ('/buwan/styles/../README.md', '/buwan/README.md', '/buwan/styles/components.css'):
            self.assertNotEqual(self.get(path).status, 200, path)

    def test_page_loads_tokens_before_product_styles(self):
        html = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('/buwan/styles/tokens.css'), html.index('/style.css'))

    def test_product_styles_use_tokens_only(self):
        css = re.sub(r'/\*.*?\*/', '', STYLE, flags=re.S)
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(', css), [], '颜色只能来自规范 Token')
        outside_media = re.sub(r'@media[^{]*', '', css)
        self.assertEqual(re.findall(r'\b\d*\.?\d+px\b', outside_media.replace('0px)', ')')), [], 'px 只允许出现在媒体查询里')
        used = set(re.findall(r'var\((--bw-[a-z0-9_-]+)', css))
        defined = set(re.findall(r'(--bw-[a-z0-9_-]+)\s*:', TOKENS))
        self.assertEqual(sorted(used - defined), [], '引用了规范里没有登记的 Token')


if __name__ == '__main__':
    unittest.main()
