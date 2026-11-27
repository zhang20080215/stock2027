import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from app.paper import tick, plan_for, parse_quotes, UTC
from app.paper_levels import exit_levels
from app.server import Application, holding_values
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
        self.assertEqual(100, orders[0]['shares'])
        with connect(self.path) as db:
            self.assertEqual(98000, db.execute("SELECT cash FROM paper_accounts WHERE market='a'").fetchone()[0])
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
        self.assertEqual(100, self.orders()[0]['shares'])

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
        self.assertEqual(200, orders[1]['shares'])

    def test_api_keeps_accounts_separate_and_reports_staleness(self):
        self.add()
        tick(self.path, self.now, self.quote)
        data = Application(self.path, 'unused').api('paper', {})
        self.assertEqual(2, len(data['accounts']))
        self.assertEqual(1, len(data['orders']))
        self.assertEqual('filled', data['orders'][0]['status'])
        self.assertEqual(2000, data['orders'][0]['market_value'])
        self.assertEqual(0, data['orders'][0]['pnl_pct'])
        self.assertEqual(2, data['orders'][0]['position_pct'])

    def test_ledger_quotes_do_not_create_simulated_orders(self):
        with connect(self.path) as db:
            db.execute('INSERT INTO meta VALUES(?,?)', ('positions', json.dumps([{'code': 'sh600999', 'name': '合成持仓', 'cost': 10, 'shares': 100}, {'code': 'hk00001', 'cost': 12, 'shares': 200}])))
        def provider(symbols, now):
            self.assertEqual(['hk00001', 'sh600999'], symbols)
            return {'sh600999': self.quote(['sh600999'], now)['sh600999']}
        tick(self.path, self.now, provider)
        items = Application(self.path, 'unused').api('positions', {})['items']
        self.assertEqual(2000, items[0]['market_value'])
        self.assertEqual(1000, items[0]['pnl'])
        self.assertEqual(100, items[0]['pnl_pct'])
        self.assertIsNone(items[0]['position_pct'])
        self.assertIsNone(items[1]['market_value'])
        self.assertIsNone(items[1]['pnl'])
        self.assertEqual([], self.orders())

    def test_unknown_or_invalid_valuation_is_not_zero_profit(self):
        self.assertIsNone(holding_values(10, 100, None)['pnl'])
        self.assertIsNone(holding_values(0, 100, 20)['pnl_pct'])
        self.assertIsNone(holding_values(10, 100, float('nan'))['market_value'])

    def test_parse_quote_requires_valid_time_and_lot(self):
        fields = ['0'] * 65
        fields[3], fields[19], fields[30], fields[60] = '20', '20.1', '2026/11/25 10:00:00', '500'
        fields[9] = '19.95'
        text = 'v_hk00981="' + '~'.join(fields) + '";'
        quote = parse_quotes(text, self.now)['hk00981']
        self.assertEqual(500, quote['lot'])
        self.assertEqual(20.1, quote['ask'])
        self.assertEqual(19.95, quote['bid'])
        fields[9] = '--'
        one_sided = parse_quotes('v_hk00981="' + '~'.join(fields) + '";', self.now)['hk00981']
        self.assertEqual((0, 20.1), (one_sided['bid'], one_sided['ask']))
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

    def test_recommended_shares_use_cash_and_resize_to_whole_lots(self):
        self.add('first', self.stock(shares='200股', position='8.2%'))
        self.add('second', self.stock(code='600002', shares='200股', position='8.2%'))
        with connect(self.path) as db:
            db.execute("UPDATE paper_accounts SET cash=24699 WHERE market='a'")
        def quote(symbols, now):
            quotes = self.quote(symbols, now)
            for q in quotes.values():
                q['ask'] = 82.33
            return quotes
        tick(self.path, self.now, quote)
        self.assertEqual([200, 100], [order['shares'] for order in self.orders()])
        with connect(self.path) as db:
            self.assertEqual(0, db.execute("SELECT cash FROM paper_accounts WHERE market='a'").fetchone()[0])

    def test_insufficient_cash_waits_for_one_lot(self):
        self.add()
        with connect(self.path) as db:
            db.execute("UPDATE paper_accounts SET cash=1000 WHERE market='a'")
        tick(self.path, self.now, self.quote)
        self.assertEqual('pending', self.orders()[0]['status'])
        with connect(self.path) as db:
            self.assertEqual(1000, db.execute("SELECT cash FROM paper_accounts WHERE market='a'").fetchone()[0])
            db.execute("UPDATE paper_accounts SET cash=2000 WHERE market='a'")
        tick(self.path, self.now, self.quote)
        self.assertEqual(100, self.orders()[0]['shares'])

    def exit_quote(self, last, bid, age=0):
        def provider(symbols, now):
            quotes = self.quote(symbols, now - dt.timedelta(seconds=age))
            for quote in quotes.values():
                quote.update(price=last, bid=bid)
            return quotes
        return provider

    def test_t1_stop_sells_at_bid_once_and_preserves_purchase(self):
        self.add(stock=self.stock(stop='18.5', target='第一目标 22，第二目标 24'))
        tick(self.path, self.now, self.quote)
        tick(self.path, self.now + dt.timedelta(minutes=1), self.exit_quote(18, 17.9))
        self.assertEqual('filled', self.orders()[0]['status'])
        tomorrow = self.now + dt.timedelta(days=1)
        for provider in (self.exit_quote(18.5, 18.4, age=121), self.exit_quote(18.5, 0), self.exit_quote(19, 18.4)):
            tick(self.path, tomorrow, provider)
            self.assertEqual('filled', self.orders()[0]['status'])
        for _ in range(2):
            tick(self.path, tomorrow, self.exit_quote(18.5, 18.4))
        data = Application(self.path, 'unused').api('paper', {})
        order = data['orders'][0]
        self.assertEqual(('sold', 20), (order['status'], order['price']))
        sale = order['sale']
        self.assertEqual(('止损', 18.5, 18.4, 100, -160),
                         (sale['reason'], sale['threshold'], sale['price'], sale['shares'], sale['realized_pnl']))
        account, hk = data['accounts']
        self.assertEqual((0, 99840, -160), (account['value'], account['cash'], account['pnl']))
        self.assertEqual(100000, hk['cash'])
        self.add('new-signal', time=tomorrow)
        tick(self.path, tomorrow, self.quote)
        self.assertEqual({'new': 'sold', 'new-signal': 'filled'}, {o['request_id']: o['status'] for o in self.orders()})

    def test_hk_profit_uses_original_first_target_on_later_quote(self):
        self.add(stock=self.stock(code='00981', target='22 / 24'))
        tick(self.path, self.now, self.exit_quote(22, 21.9))
        with connect(self.path) as db:
            db.execute('UPDATE model_results SET data=?', (json.dumps({'stocks': [self.stock(code='00981', target='40')]}),))
        tick(self.path, self.now.replace(hour=4), self.exit_quote(23, 22.9))
        self.assertEqual('filled', self.orders()[0]['status'])
        tick(self.path, self.now.replace(hour=5), self.exit_quote(22, 21.95))
        order = Application(self.path, 'unused').api('paper', {})['orders'][0]
        self.assertEqual(('止盈', 22, 21.95), (order['sale']['reason'], order['sale']['threshold'], order['sale']['price']))

    def test_legacy_holding_reads_only_its_ticker_from_original_plan(self):
        self.add()
        tick(self.path, self.now, self.quote)
        original = '~~~\n标的：测试 600002\n止损：5\n第一目标：6\n~~~\n~~~\n标的：测试 600001\n止损：18.5（-7.5%）\n第一目标：22（20日高点）\n~~~'.replace('~~~', '\x60' * 3)
        with connect(self.path) as db:
            db.execute('UPDATE model_results SET text=?', (original,))
            db.execute('UPDATE paper_orders SET plan=?', (json.dumps({'source': self.stock()}),))
        tick(self.path, self.now, self.quote)
        plan = json.loads(self.orders()[0]['plan'])
        self.assertEqual({'stop': 18.5, 'target': 22}, plan['exit_levels'])

    def test_conditional_and_non_price_values_do_not_trigger(self):
        for value in ('5%', '5 %', '1.5倍ATR', '18-20', '收盘跌破18', '18元（收盘确认）', '22 元卖出一半', '18:1'):
            with self.subTest(value=value):
                self.assertIsNone(exit_levels({'stop': value}, '600001', '')['stop'])
        self.assertEqual({'stop': None, 'target': 22}, exit_levels({'target': '22 / 24'}, '600001', ''))

    def test_original_table_with_explicit_take_profit_header(self):
        row = '| 测试甲 600001 | 推荐 | 18.5 | 22 |'
        table = '| 股票 | 结论 | 止损价 | 止盈价 |\n|---|---|---|---|\n' + row
        self.assertEqual({'stop': 18.5, 'target': 22}, exit_levels({'source_line': row}, '600001', table))


if __name__ == '__main__':
    unittest.main()
