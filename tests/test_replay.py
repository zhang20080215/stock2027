import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from app.replay import replay, quote_time
from app.store import connect
from app.paper import UTC


class ReplayTest(unittest.TestCase):
    def test_tushare_uses_only_complete_post_signal_minutes_without_text_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(str(Path(tmp) / 'test.db'))
            self.addCleanup(db.close)
            stock = {'name':'测试甲','code':'600001','decision':'推荐','position':'10%仓位','entry':'20–21','source_text':'现价20.0'}
            db.execute('INSERT INTO model_events VALUES(?,?,?,?,?,?,?,?,?)', ('r','s','u','选股','2026-11-23T02:00:30+00:00','test','test','合成',json.dumps({'stocks':[stock],'summary':{}})))
            start,end=dt.datetime(2026,11,1,tzinfo=UTC),dt.datetime(2026,11,25,tzinfo=UTC)
            self.assertEqual('missing',replay(db,start,end,'tushare')['orders'][0]['status'])
            for timestamp,price in [('2026-11-23T10:00:00+08:00',20),('2026-11-23T10:01:00+08:00',21)]:
                db.execute('INSERT INTO history_bars VALUES(?,?,?,?,?,?)',('tushare','a','600001',timestamp,price,100))
            result=replay(db,start,end,'tushare')['orders'][0]
            self.assertEqual(21,result['price'])
            self.assertEqual('2026-11-23T02:02:00+00:00',result['filled'])
            self.assertEqual('tushare_1min',result['price_basis'])

    def test_later_retraction_does_not_erase_earlier_recommendation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(str(Path(tmp) / 'test.db'))
            self.addCleanup(db.close)
            stock = {'name': '测试甲', 'code': '600001', 'decision': '推荐', 'position': '10%仓位', 'entry': '20–21附近', 'source_text': '推荐：测试甲600001\n现价20.5'}
            for ident, timestamp, stocks in [('first','2026-11-23T02:00:00+00:00',[stock]),('later','2026-11-23T03:00:00+00:00',[])]:
                db.execute('INSERT INTO model_events VALUES(?,?,?,?,?,?,?,?,?)', (ident,'s','same-request','选股',timestamp,'test','test','合成',json.dumps({'stocks':stocks,'summary':{}})))
            report = replay(db, dt.datetime(2026,11,1,tzinfo=UTC),dt.datetime(2026,11,25,tzinfo=UTC))
            self.assertEqual(2,report['result_count'])
            self.assertEqual('first',report['orders'][0]['result_id'])
            self.assertEqual('model_quote',report['orders'][0]['price_basis'])
            self.assertEqual('estimated',report['orders'][0]['status'])
            self.assertTrue(report['orders'][0]['mark_missing'])

    def test_snapshot_estimate_does_not_write_live_ledger_or_use_past_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(str(Path(tmp) / 'test.db'))
            self.addCleanup(db.close)
            stock = {'name': '测试甲', 'code': '600001', 'decision': '推荐', 'position': '10%仓位', 'entry': '20–21'}
            db.execute('INSERT INTO model_results VALUES(?,?,?,?,?,?,?,?,?)', ('r', 's', 'u', '选股', '2026-11-23T02:00:00+00:00', 'test', 'test', '合成', json.dumps({'stocks': [stock], 'summary': {}})))
            for minute, price in [('095900', 20), ('100100', 21), ('110000', 22)]:
                q = {'code': '600001', 'ts': '20261123' + minute, 'price': price}
                timestamp = quote_time(q['ts']).isoformat()
                db.execute('INSERT INTO scans VALUES(?,?,?,?,?,?)', (minute, timestamp, 'a', json.dumps({'stocks': [q]}), None, None))
            result = replay(db, dt.datetime(2026, 11, 1, tzinfo=UTC), dt.datetime(2026, 11, 25, tzinfo=UTC))
            order = result['orders'][0]
            self.assertEqual('estimated', order['status'])
            self.assertEqual(21, order['price'])
            self.assertEqual(400, order['shares'])
            self.assertEqual(400, order['pnl'])
            self.assertEqual(100400, result['accounts'][0]['equity'])
            self.assertEqual(0, db.execute('SELECT count(*) FROM paper_orders').fetchone()[0])
            self.assertEqual(0, db.execute('SELECT count(*) FROM paper_accounts').fetchone()[0])

    def test_missing_history_does_not_fabricate_a_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(str(Path(tmp) / 'test.db'))
            self.addCleanup(db.close)
            stock = {'name': '测试甲', 'code': '600001', 'decision': '推荐', 'position': '10%仓位', 'entry': '20–21'}
            db.execute('INSERT INTO model_results VALUES(?,?,?,?,?,?,?,?,?)', ('r', 's', 'u', '选股', '2026-11-23T02:00:00+00:00', 'test', 'test', '合成', json.dumps({'stocks': [stock], 'summary': {}})))
            result = replay(db, dt.datetime(2026, 11, 1, tzinfo=UTC), dt.datetime(2026, 11, 25, tzinfo=UTC))
            self.assertEqual('missing', result['orders'][0]['status'])
            self.assertIsNone(result['orders'][0]['price'])
            self.assertEqual(100000, result['accounts'][0]['cash'])
