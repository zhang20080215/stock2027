import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.paper_capital import amounts, apply_config, save_config
from app.server import Application, handler
from app.store import connect


class CapitalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'paper.db')
        self.config = Path(self.tmp.name) / 'capital.json'
        with connect(self.path) as db:
            db.executemany('INSERT INTO paper_accounts VALUES(?,?,?)', [('a', 100000, 90000), ('hk', 100000, 80000)])

    def test_independent_cash_adjustment_is_idempotent(self):
        save_config(self.config, {'a': 200000, 'hk': 150000})
        with connect(self.path) as db:
            apply_config(db, self.config)
            apply_config(db, self.config)
            self.assertEqual([(200000, 190000), (150000, 130000)], [tuple(r) for r in db.execute('SELECT initial,cash FROM paper_accounts ORDER BY market')])
        save_config(self.config, {'a': 50000, 'hk': 150000})
        with connect(self.path) as db:
            apply_config(db, self.config)
            self.assertEqual(40000, db.execute("SELECT cash FROM paper_accounts WHERE market='a'").fetchone()[0])

    def test_both_accounts_unchanged_when_one_cannot_be_reduced(self):
        save_config(self.config, {'a': 200000, 'hk': 1000})
        with connect(self.path) as db:
            apply_config(db, self.config)
            self.assertEqual([(100000, 90000), (100000, 80000)], [tuple(r) for r in db.execute('SELECT initial,cash FROM paper_accounts ORDER BY market')])
            self.assertEqual('error', json.loads(db.execute("SELECT value FROM meta WHERE key='paper_capital_config'").fetchone()[0])['status'])

    def test_reject_invalid_capital(self):
        for value in (0, -1, 'NaN', 'Infinity', True, '1.001', 1000000000001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                amounts({'a': value, 'hk': 100000})

    def test_http_auth_origin_and_readonly_ledger_boundary(self):
        app = Application(self.path, 'unused')
        app.paper_config_file = str(self.config)
        app.sessions['test'] = time.time() + 60
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = 'http://127.0.0.1:%d/api/paper-capital' % server.server_port
        body = json.dumps({'initial': {'a': 200000, 'hk': 150000}}).encode()
        for headers, status in (({}, 401), ({'Cookie': 'stock2027=test', 'Origin': 'http://foreign.invalid'}, 403)):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url, data=body, headers=headers))
            self.assertEqual(status, error.exception.code)
        with urlopen(Request(url, data=body, headers={'Cookie': 'stock2027=test'})) as response:
            self.assertEqual(200, response.status)
        self.assertTrue(self.config.exists())
        with connect(self.path, readonly=True) as db:
            self.assertEqual(90000, db.execute("SELECT cash FROM paper_accounts WHERE market='a'").fetchone()[0])
