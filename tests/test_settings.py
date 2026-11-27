import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.server import Application, handler


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'settings.json'
        self.app = Application('unused', 'private-password-hash', master_password='private-master-hash')
        self.app.settings_file = str(self.path)

    def test_name_survives_restart_without_exposing_credentials(self):
        self.assertEqual({'product_name': '产品'}, self.app.branding())
        self.assertEqual(200, self.app.save_settings('  示例产品  ')[0])
        restarted = Application('unused', 'other-private-hash')
        restarted.settings_file = str(self.path)
        self.assertEqual({'product_name': '示例产品'}, restarted.branding())
        self.assertEqual({'product_name': '示例产品'}, json.loads(self.path.read_text()))

    def test_invalid_names_preserve_saved_name(self):
        self.app.save_settings('示例产品')
        for name in ('', '   ', 'x' * 33, 'hello\nworld', '\x00', None, 123):
            with self.subTest(name=name):
                self.assertEqual(400, self.app.save_settings(name)[0])
                self.assertEqual('示例产品', self.app.branding()['product_name'])

    def test_corrupt_settings_fall_back_to_default(self):
        self.path.write_text('not json')
        self.assertEqual({'product_name': '产品'}, self.app.branding())

    def test_public_branding_and_authenticated_settings(self):
        self.app.sessions['test'] = time.time() + 60
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.app))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:%d' % server.server_port
        with urlopen(base + '/api/branding') as response:
            self.assertEqual({'product_name': '产品'}, json.load(response))
        body = json.dumps({'product_name': '新产品'}).encode()
        for headers, expected in (({}, 401), ({'Cookie': 'stock2027=test', 'Origin': 'http://foreign.invalid'}, 403)):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + '/api/settings', data=body, headers=headers))
            self.assertEqual(expected, error.exception.code)
        with urlopen(Request(base + '/api/settings', data=body, headers={'Cookie': 'stock2027=test'})) as response:
            self.assertEqual({'product_name': '新产品'}, json.load(response))
        with urlopen(base + '/api/branding') as response:
            self.assertEqual({'product_name': '新产品'}, json.load(response))
