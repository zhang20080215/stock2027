"""Historical snapshot estimates, isolated from the forward paper ledger."""
import argparse
from collections import Counter
from contextlib import closing
import datetime as dt
import json
import math
import re

from app.paper import LOCAL, UTC, plan_for, stamp, trading
from app.store import connect


def quote_time(value):
    if re.fullmatch(r'\d{14}', str(value)):
        return dt.datetime.strptime(value, '%Y%m%d%H%M%S').replace(tzinfo=LOCAL).astimezone(UTC)
    parsed = dt.datetime.fromisoformat(str(value).replace('/', '-').replace('Z', '+00:00'))
    return (parsed.replace(tzinfo=LOCAL) if parsed.tzinfo is None else parsed).astimezone(UTC)


def replay(db, start, end, provider='legacy'):
    accounts = {m: {'market': m, 'initial': 100000, 'cash': 100000, 'value': 0} for m in ('a', 'hk')}
    quotes, orders, holdings = {}, [], {}
    table = 'model_events' if db.execute('SELECT count(*) FROM model_events').fetchone()[0] else 'model_results'
    results = [r for r in db.execute('SELECT * FROM ' + table + ' ORDER BY timestamp,id') if start <= stamp(r['timestamp']) < end]
    for scan in db.execute('SELECT timestamp,market,data FROM scans ORDER BY timestamp'):
        for q in json.loads(scan['data']).get('stocks', []):
            try:
                t = quote_time(q['ts'])
                available = max(t, stamp(scan['timestamp']))
                price = float(q['price'])
                code = re.sub(r'^(?:sh|sz|hk)', '', str(q['code']))
                if not start <= available < end or not math.isfinite(price) or price <= 0:
                    continue
                # A stale quote cannot become a new observation merely by being re-imported.
                if (available - t).total_seconds() > 120:
                    continue
                quotes.setdefault((scan['market'], code), []).append({'time': t, 'available': available, 'price': price})
            except (ValueError, KeyError, TypeError):
                continue
    for series in quotes.values():
        series.sort(key=lambda q: q['available'])
    if provider == 'tushare':
        quotes = {}
        for bar in db.execute("SELECT * FROM history_bars WHERE provider='tushare' ORDER BY time"):
            when = stamp(bar['time'])
            # Wait an additional minute so the entire candle has ended, irrespective
            # of whether the feed labels minute starts or minute ends. Never use H/L.
            available = when + dt.timedelta(minutes=1)
            if start <= available < end and bar['close'] > 0 and bar['volume'] > 0:
                quotes.setdefault((bar['market'], bar['code']), []).append({'time': available, 'available': available, 'price': bar['close'], 'basis': 'tushare_1min', 'bar_time': bar['time']})
    events = []
    for result_index, r in enumerate(results):
        data = json.loads(r['data'])
        stocks = data.get('stocks', [])
        counts = Counter(re.sub(r'^(?:sh|sz|hk)', '', s.get('code') or '') for s in stocks)
        for s in stocks:
            code = re.sub(r'^(?:sh|sz|hk)', '', s.get('code') or '')
            market = 'hk' if len(code) == 5 else 'a'
            # "附近" is retained as an approximate source price, never assigned an invented tolerance.
            normalized = dict(s, entry=(s.get('entry') or '').replace('附近', ''))
            plan, reason = plan_for(normalized, data.get('summary', {}))
            if counts[code] > 1:
                plan, reason = None, '同一回复有多条同股计划，需核对'
            order = {'result_id': r['id'], 'signal_time': r['timestamp'], 'name': s.get('name'), 'code': code, 'market': market, 'position': s.get('position'), 'entry': s.get('entry'), 'status': 'review', 'reason': reason, 'price': None, 'shares': None, 'filled': None}
            orders.append(order)
            if not plan:
                continue
            if market == 'hk':
                order['reason'] = 'Tushare港股分钟权限未开通' if provider == 'tushare' else '历史快照缺少每手股数及卖一报价'
                continue
            if not code.startswith(('600', '601', '603', '605', '688', '000', '001', '002', '003', '300', '301')):
                order['reason'] = '当前不支持该市场交易单位'
                continue
            signal = stamp(r['timestamp'])
            source = s.get('source_text') or ''
            reference = re.search(r'现价\s*[：:]?\s*(\d+(?:\.\d+)?)', source)
            if not reference:
                reference = re.search(r'^[^\n]*\d{5,6}\s*[）)]?\s*[·@]\s*(\d+(?:\.\d+)?)', source)
            reference_price = float(reference[1]) if reference else None
            # Independent estimate at publication time, not a later day's final conclusion.
            if provider != 'tushare' and reference_price and trading(signal, market) and reference_price >= plan['low'] and (plan['high'] is None or reference_price <= plan['high']):
                q = {'time': signal, 'available': signal, 'price': reference_price, 'basis': 'model_quote'}
                events.append((signal, r['id'], order, plan, q))
                continue
            next_reply = stamp(results[result_index + 1]['timestamp']) if result_index + 1 < len(results) else end
            candidates = [q for q in quotes.get((market, code), []) if q['time'] >= signal and (provider != 'tushare' or stamp(q['bar_time']) > signal) and q['available'] < next_reply and q['available'].astimezone(LOCAL).date() == signal.astimezone(LOCAL).date() and trading(q['available'], market)]
            if not candidates:
                order['status'], order['reason'] = 'missing', '建议后至下一次回复前无有效Tushare分钟数据' if provider == 'tushare' else '建议后当日无有效历史快照'
                continue
            matching = [q for q in candidates if q['price'] >= plan['low'] and (plan['high'] is None or q['price'] <= plan['high'])]
            if not matching:
                order['status'], order['reason'] = 'unmet', '有效分钟收盘价未落入模型买入区间' if provider == 'tushare' else '已有快照未落入模型买入区间'
                continue
            q = matching[0]
            events.append((q['available'], r['id'], order, plan, q))
    source_by_id = {r['id']: r['text'] for r in results}
    for when, _, order, plan, quote in sorted(events, key=lambda e: (e[0], e[1])):
        key = (order['market'], order['code'])
        if key in holdings:
            order['status'], order['reason'] = 'skipped', '已有模拟持仓，不重复加仓'
            continue
        paragraphs = source_by_id.get(order['result_id'], '').split('\n\n')
        exclusive = any(held_key[0] == order['market'] and any((held.get('name') or '')[:2] in paragraph and (order.get('name') or '')[:2] in paragraph and re.search(r'二选一|只能选一个|不要再|不要买第二只', paragraph) for paragraph in paragraphs) for held_key, held in holdings.items())
        if exclusive:
            order['status'], order['reason'] = 'skipped', '当次原文限制二选一，账户已有另一只持仓'
            continue
        account = accounts[order['market']]
        equity = account['cash']
        for held_key, held in holdings.items():
            if held_key[0] == order['market']:
                marks = [q for q in quotes.get(held_key, []) if q['available'] <= when]
                # No future quote is used for historical sizing.
                equity += held['shares'] * (marks[-1]['price'] if marks else held['price'])
        price = quote['price']
        budget = equity * plan['ratio']
        shares = math.floor(budget / price) if order['code'].startswith('688') else math.floor(budget / price / 100) * 100
        minimum = 200 if order['code'].startswith('688') else 100
        cost = round(shares * price, 2)
        if shares < minimum or cost > account['cash']:
            order['status'], order['reason'] = 'skipped', '仓位不足交易单位或可用资金不足'
            continue
        account['cash'] = round(account['cash'] - cost, 2)
        order.update(status='estimated', reason='Tushare已结束分钟收盘价估算，非逐笔成交' if quote.get('basis') == 'tushare_1min' else '按当次原文现价估算，无历史盘口验证' if quote.get('basis') == 'model_quote' else '按当时快照价格估算，非真实撮合', price_basis=quote.get('basis', 'snapshot'), price=price, shares=shares, filled=when.isoformat(), quote_time=quote['time'].isoformat())
        holdings[key] = order
    for key, held in holdings.items():
        series = quotes.get(key, [])
        series = [q for q in series if q['available'] >= stamp(held['filled'])]
        mark = series[-1] if series else None
        held['mark'] = mark['price'] if mark else held['price']
        held['mark_time'] = mark['time'].isoformat() if mark else held['filled']
        held['mark_missing'] = mark is None
        if mark is None:
            accounts[key[0]]['valuation_incomplete'] = True
        held['pnl'] = round((held['mark'] - held['price']) * held['shares'], 2)
        accounts[key[0]]['value'] += held['shares'] * held['mark']
    for account in accounts.values():
        account['value'] = round(account['value'], 2)
        account['equity'] = round(account['cash'] + account['value'], 2)
        account['pnl'] = round(account['equity'] - account['initial'], 2)
    assumptions = '逐次按真实回复时间处理，保留所有完整回复，不按日终结论筛选。'
    assumptions += '仅用Tushare一分钟行情，取推荐后完整分钟收盘价，额外等待一分钟；不使用原文现价或扫描快照补单。不通过分钟最高最低价推断成交。港股分钟无权限。' if provider == 'tushare' else '优先按原文现价估算，其次使用发布后历史快照。'
    assumptions += '每次建议待单最多保留到下一次选股回复。仅买入持有，不自动卖出；同股重复建议不加仓，二选一按当时持仓处理。100000元/100000港元独立资金，未计费用；分钟价格估算不代表盘口真实成交。'
    return {'kind': 'per_reply_estimate', 'provider': provider, 'start': start.isoformat(), 'end_exclusive': end.isoformat(), 'generated': dt.datetime.now(UTC).isoformat(), 'result_count': len(results), 'stock_count': len(orders), 'counts': dict(Counter(o['status'] for o in orders)), 'accounts': list(accounts.values()), 'orders': orders, 'events': [{'id': r['id'], 'timestamp': r['timestamp'], 'prompt': r['prompt'], 'stock_count': len(json.loads(r['data']).get('stocks', []))} for r in results], 'assumptions': assumptions}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--start', default='2026-11-01')
    parser.add_argument('--end', default='2026-11-25')
    parser.add_argument('--provider', choices=('legacy', 'tushare'), default='legacy')
    args = parser.parse_args()
    start = dt.datetime.combine(dt.date.fromisoformat(args.start), dt.time(), LOCAL).astimezone(UTC)
    end = dt.datetime.combine(dt.date.fromisoformat(args.end) + dt.timedelta(days=1), dt.time(), LOCAL).astimezone(UTC)
    with closing(connect(args.db)) as db:
        report = replay(db, start, end, args.provider)
        with db:
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('paper_replay', json.dumps(report, ensure_ascii=False)))
    print(json.dumps({key: report[key] for key in ('result_count', 'stock_count', 'counts', 'accounts')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
