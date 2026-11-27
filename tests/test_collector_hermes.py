import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.collector_hermes import sync
from app.server import Application


class HermesCollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.hermes = root / '.hermes'
        (self.hermes / 'memories').mkdir(parents=True)
        (self.hermes / 'plans').mkdir(parents=True)
        (self.hermes / 'skills' / 'stock-scan').mkdir(parents=True)
        (self.hermes / 'cron').mkdir(parents=True)
        self.db_path = str(root / 'console.db')
        self.positions = root / 'positions.json'
        self.positions.write_text(json.dumps([{'name': '中芯国际', 'code': '688981', 'shares': 200, 'cost': 90.0}]), encoding='utf-8')
        (self.hermes / 'memories' / 'MEMORY.md').write_text('# 记忆\napi_key=secret-123\n', encoding='utf-8')
        (self.hermes / 'plans' / 'short-term-ledger.md').write_text('# 短线台账\n记录', encoding='utf-8')
        (self.hermes / 'SOUL.md').write_text('核心原则', encoding='utf-8')
        (self.hermes / 'skills' / 'stock-scan' / 'SKILL.md').write_text('选股技能', encoding='utf-8')
        (self.hermes / 'cron' / 'jobs.json').write_text(json.dumps({'jobs': [{'id': 'j1', 'name': '早盘选股', 'enabled': True, 'schedule': {'expr': '0 9 * * 1-5'}}]}), encoding='utf-8')

        with sqlite3.connect(self.hermes / 'state.db') as src:
            src.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT, model TEXT, started_at REAL, ended_at REAL, message_count INTEGER, title TEXT, last_activity_at REAL)')
            src.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT, tool_call_id TEXT, timestamp REAL, active INTEGER)')
            src.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)', ('s1', 'weixin', 'deepseek-chat', 1732500000.0, 1732500100.0, 4, '早盘选股', 1732500100.0))
            calls = json.dumps([{'id': 'c1', 'function': {'name': 'terminal', 'arguments': json.dumps({'command': 'python3 a-scan.py'})}}])
            src.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)', [
                (1, 's1', 'user', '今天A股选股', None, None, None, 1732500001.0, 1),
                (2, 's1', 'assistant', '', calls, None, None, 1732500005.0, 1),
                (3, 's1', 'tool', json.dumps({'output': '候选 600001', 'exit_code': 0}), None, 'terminal', 'c1', 1732500010.0, 1),
                (4, 's1', 'assistant', '**推荐：测试甲 600001**\n- 买入：20，100股\n- 止损：18.5\n- 目标：23', None, None, None, 1732500020.0, 1),
            ])

    def test_sync_populates_console_tables_and_redacts_secrets(self):
        info = sync(self.db_path, self.hermes, self.positions)
        self.assertEqual([], info['errors'])
        self.assertGreaterEqual(info['imported'], 3)

        app = Application(self.db_path, 'unused')
        overview = app.api('overview', {})
        self.assertEqual(1, overview['counts']['sessions'])
        self.assertEqual(2, overview['counts']['messages'])
        self.assertEqual(1, overview['counts']['reports'])
        self.assertEqual(2, overview['counts']['documents'])
        self.assertEqual(1, overview['positions_count'])

        results = app.api('model-results', {})
        self.assertEqual(1, len(results))
        self.assertEqual(1, results[0]['stock_count'])

        project = app.api('project', {})
        self.assertEqual(2, len(project['rules']))
        self.assertEqual('available', project['scheduler']['status'])

        mem = app.api('documents', {'id': ['MEMORY.md']})
        self.assertIn('[已隐藏]', mem['text'])
        self.assertNotIn('secret-123', mem['text'])


if __name__ == '__main__':
    unittest.main()
