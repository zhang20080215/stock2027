import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from app.paper import tick, plan_for, parse_quotes, UTC
from app.server import Application
from app.store import connect


class PaperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'paper.db')
        self.now = dt.datetime(2026, 11, 25, 2, 0, tzinfo=UTC)
        tick(self.path, self.now, lambda symbols, now: {})

    def stock(self, **changes):
        return dict({'name': '测试甲', 'code': '600001', 'decision': '推荐', 'position': '10%仓位', 'entry': '20–21，100股'}, **changes)

    def add(self, identifier='new', stock=None, time=None):
        with connect(self.path) as db:
            db.execute('INSERT INTO model_results VALUES(?,?,?,?,?,?,?,?,?)', (identifier, 's', identifier, '选股', (time or self.now).isoformat(), 'test', 'test', '合成测试', json.dumps({'stocks': [stock or self.stock()], 'summary': {}})))

    def quote(self, symbols, now):
        return {s: {'price': 20, 'ask': 20, 'timestamp': now.isoformat(), 'lot': 100, 'fetched': now.isoformat()} for s in symbols}

    def orders(self):
        with connect(self.path) as db:
            return [dict(row) for row in db.execute('SELECT * FROM paper_orders ORDER BY id')]

    def test_history_excluded_and_fill_once_per_request(self):
        self.add('old', time=self.now - dt.timedelta(days=1))
        self.add()
        tick(self.path, self.now, self.quote)
        tick(self.path, self.now, self.quote)
        orders = self.orders()
        self.assertEqual(1, len(orders))
        self.assertEqual('filled', orders[0]['status'])
        self.assertEqual(500, orders[0]['shares'])
        with connect(self.path) as db:
            self.assertEqual(90000, db.execute("SELECT cash FROM paper_accounts WHERE market='a'").fetchone()[0])
            self.assertEqual(100000, db.execute("SELECT cash FROM paper_accounts WHERE market='hk'").fetchone()[0])

    def test_unsafe_language_or_missing_allocation_never_executes(self):
        for changes in ({'decision': '次选不给买点'}, {'entry': '如果回踩20元再买'}, {'position': None}, {'position': '最多10%'}, {'entry': '20或21或22买入'}):
            self.assertIsNone(plan_for(self.stock(**changes), {})[0])
        self.assertIsNone(plan_for(self.stock(), {'participation': '空仓'})[0])

    def test_stale_quotes_and_no_ask_do_not_fill(self):
        self.add()
        def old(symbols, now):
            quotes = self.quote(symbols, now)
            for q in quotes.values():
                q['timestamp'] = (now - dt.timedelta(minutes=15)).isoformat()
            return quotes
        tick(self.path, self.now, old)
        self.assertEqual('pending', self.orders()[0]['status'])
        def no_ask(symbols, now):
            quotes = self.quote(symbols, now)
            for q in quotes.values():
                q['ask'] = 0
            return quotes
        tick(self.path, self.now, no_ask)
        self.assertEqual('pending', self.orders()[0]['status'])

    def test_price_bounds_expiry_and_duplicate_holding(self):
        self.add(stock=self.stock(entry='18–19'))
        tick(self.path, self.now, lambda symbols, now: {})
        self.assertEqual('pending', self.orders()[0]['status'])
        tick(self.path, self.now + dt.timedelta(days=1), self.quote)
        self.assertEqual('expired', self.orders()[0]['status'])
        self.add('second', time=self.now + dt.timedelta(days=1))
        tick(self.path, self.now + dt.timedelta(days=1), self.quote)
        self.add('third', time=self.now + dt.timedelta(days=1))
        tick(self.path, self.now + dt.timedelta(days=1), self.quote)
        self.assertEqual(['expired', 'filled', 'skipped'], [o['status'] for o in self.orders()])

    def test_recommendation_buys_market_without_entry_or_position(self):
        self.add(stock=self.stock(entry='如果回踩18再买', position=None))
        tick(self.path, self.now, self.quote)
        self.assertEqual('filled', self.orders()[0]['status'])
        self.assertEqual(20, self.orders()[0]['price'])
        self.assertEqual(500, self.orders()[0]['shares'])

    def test_review_recommendation_is_reactivated_but_alternative_is_not(self):
        self.add()
        self.add('alternative', self.stock(code='600002', decision='备选'))
        tick(self.path, self.now, lambda symbols, now: {})
        with connect(self.path) as db:
            db.execute("UPDATE paper_orders SET status='review' WHERE request_id='new'")
        tick(self.path, self.now, self.quote)
        self.assertEqual(['review', 'filled'], [o['status'] for o in self.orders()])

    def test_hk_lots_and_star_minimum(self):
        self.add('hk', self.stock(code='00981'))
        self.add('star', self.stock(code='688001', position='1%仓位'))
        def lots(symbols, now):
            quotes = self.quote(symbols, now)
            quotes['hk00981']['lot'] = 400
            return quotes
        tick(self.path, self.now, lots)
        orders = self.orders()
        self.assertEqual(400, orders[0]['shares'])
        self.assertEqual('skipped', orders[1]['status'])

    def test_api_keeps_accounts_separate_and_reports_staleness(self):
        self.add()
        tick(self.path, self.now, self.quote)
        data = Application(self.path, 'unused').api('paper', {})
        self.assertEqual(2, len(data['accounts']))
        self.assertEqual(1, len(data['orders']))
        self.assertEqual('filled', data['orders'][0]['status'])

    def test_parse_quote_requires_valid_time_and_lot(self):
        fields = ['0'] * 65
        fields[3], fields[19], fields[30], fields[60] = '20', '20.1', '2026/11/25 10:00:00', '500'
        text = 'v_hk00981="' + '~'.join(fields) + '";'
        quote = parse_quotes(text, self.now)['hk00981']
        self.assertEqual(500, quote['lot'])
        self.assertEqual(20.1, quote['ask'])
        fields[60] = 'unknown'
        self.assertEqual({}, parse_quotes('v_hk00981="' + '~'.join(fields) + '";', self.now))

    def test_closed_market_and_quote_before_signal_wait(self):
        self.add()
        tick(self.path, self.now.replace(hour=4), self.quote)
        self.assertEqual('pending', self.orders()[0]['status'])
        def earlier(symbols, now):
            return self.quote(symbols, now - dt.timedelta(seconds=1))
        tick(self.path, self.now, earlier)
        self.assertEqual('pending', self.orders()[0]['status'])

    def test_updated_plan_cannot_execute_old_pending_order(self):
        self.add()
        tick(self.path, self.now, lambda symbols, now: {})
        with connect(self.path) as db:
            db.execute('UPDATE model_results SET data=?', (json.dumps({'stocks': [self.stock(decision='不买')]}),))
        tick(self.path, self.now, self.quote)
        self.assertEqual('expired', self.orders()[0]['status'])

    def test_insufficient_cash_does_not_overdraw_or_shrink_order(self):
        self.add()
        with connect(self.path) as db:
            db.execute("UPDATE paper_accounts SET cash=1000 WHERE market='a'")
        tick(self.path, self.now, self.quote)
        # At 10% of equity, fewer than 100 shares are affordable.
        self.assertEqual('skipped', self.orders()[0]['status'])
        with connect(self.path) as db:
            self.assertEqual(1000, db.execute("SELECT cash FROM paper_accounts WHERE market='a'").fetchone()[0])


if __name__ == '__main__':
    unittest.main()
