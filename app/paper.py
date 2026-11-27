"""Paper execution only; never connects to a broker or writes OpenClaw sources."""
from contextlib import closing
import datetime as dt
from decimal import Decimal
import json
import logging
import math
import os
import re
import time
from urllib.request import Request, urlopen

from app.store import connect
from app.paper_levels import exit_levels

UTC = dt.timezone.utc
LOCAL = dt.timezone(dt.timedelta(hours=8))
LOG = logging.getLogger(__name__)


def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)


def symbol(market, code):
    return 'hk' + code if market == 'hk' else ('sh' if code.startswith(('6', '5')) else 'sz') + code


def plan_for(stock, summary):
    """Fail closed on ambiguous language; never derive allocation from a share count."""
    decision = stock.get('decision') or ''
    entry = stock.get('entry') or ''
    if summary.get('participation'):
        return None, '整体结论不参与'
    if decision not in ('推荐', '首选', '买入', '可以买', '可买', '明确买入', '可以买入', '可买入', '推荐买入', '立即买入', '直接买入'):
        return None, '未明确允许买入'
    if re.search(r'如果|若|等待|等到|回踩|突破|站稳|企稳|确认|不追|不买|暂不|观察|备选|次选|再买|才买|不超过', entry):
        return None, '条件性计划，待人工核对'
    position = stock.get('position') or ''
    percentages = re.findall(r'(?<![\d.])(\d+(?:\.\d+)?)\s*%', position)
    if len(percentages) != 1 or re.search(r'上限|最多|不超|以内|至|到|[-–—~～]', position):
        return None, '缺少明确仓位比例'
    ratio = float(percentages[0]) / 100
    if not 0 < ratio <= 1:
        return None, '仓位比例无效'
    if not entry:
        return None, '缺少买入计划'
    # Read prices only before sizing/allocation text, never mistake 1000 shares for a price.
    price_text = re.split(r'[，,；;\n]|\d[\d,]*\s*股', entry, maxsplit=1)[0]
    bounds = re.fullmatch(r'\s*(?:买入|现价|市价|价格|约|在|以|买入价|买入区间|买入价格)?[：:\s]*([0-9]+(?:\.[0-9]+)?)\s*(?:元|港元)?\s*[-–—~～至]\s*([0-9]+(?:\.[0-9]+)?)\s*(?:元|港元|买入)?\s*', price_text)
    single = re.fullmatch(r'\s*(?:买入|价格|约|以|买入价|买入价格)?[：:\s]*([0-9]+(?:\.[0-9]+)?)\s*(?:元|港元|买入)?\s*', price_text)
    if bounds:
        low, high = map(float, bounds.groups())
    elif single:
        low, high = 0, float(single[1])
    elif re.fullmatch(r'(?:按)?(?:当前)?(?:现价|市价)(?:立即)?(?:买入|建仓)?', price_text.strip()):
        low, high = 0, None
    else:
        return None, '买入价格表达需核对'
    if high is not None and (high <= 0 or low > high):
        return None, '买入价格无效'
    return {'ratio': ratio, 'low': low, 'high': high, 'source': stock}, None


def recommendation_plan(stock, summary):
    """Use the recommended share count when available, subject to available cash."""
    if stock.get('decision') not in ('推荐', '首选', '买入', '可以买', '可买', '明确买入', '可以买入', '可买入', '推荐买入', '立即买入', '直接买入'):
        return None, '未明确允许买入'
    quantity = re.fullmatch(r'\s*(\d{1,3}(?:,\d{3})+|\d+)\s*(?:股)?\s*', str(stock.get('shares') or ''))
    requested = int(quantity[1].replace(',', '')) if quantity else None
    return {'requested_shares': requested, 'source': stock, 'policy': 'recommendation_cash_v2'}, None


def book_price(value):
    try:
        price = float(value)
        return price if math.isfinite(price) and price > 0 else 0
    except ValueError:
        return 0


def parse_quotes(text, now):
    quotes = {}
    for sym, body in re.findall(r'v_((?:sh|sz|hk)\d+)="([^"]*)"', text):
        values = body.split('~')
        try:
            hk = sym.startswith('hk')
            timestamp = dt.datetime.strptime(values[30], '%Y/%m/%d %H:%M:%S' if hk else '%Y%m%d%H%M%S').replace(tzinfo=LOCAL).astimezone(UTC)
            price = float(values[3])
            bid, ask = book_price(values[9]), book_price(values[19])
            # HK feed's lot field; an absent/invalid field must never default to 100.
            lot = int(values[60]) if hk else 100
            if not math.isfinite(price) or price <= 0 or lot <= 0:
                continue
            quotes[sym] = {'price': price, 'ask': ask, 'bid': bid, 'timestamp': timestamp.isoformat(), 'lot': lot, 'fetched': now.isoformat()}
        except (ValueError, IndexError):
            continue
    return quotes


def fetch_quotes(symbols, now):
    quotes = {}
    for start in range(0, len(symbols), 60):
        query = ','.join(symbols[start:start + 60])
        request = Request('https://qt.gtimg.cn/q=' + query, headers={'User-Agent': 'Stock2027/1.0'})
        with urlopen(request, timeout=8) as response:
            text = response.read(1024 * 1024).decode('gb18030', errors='replace')
        quotes.update(parse_quotes(text, now))
    return quotes


def trading(now, market):
    local = now.astimezone(LOCAL)
    minute = local.hour * 60 + local.minute
    return local.weekday() < 5 and (570 <= minute < (720 if market == 'hk' else 690) or 780 <= minute < (960 if market == 'hk' else 897))


def fresh(quote, now):
    return quote and -5 <= (now - stamp(quote['timestamp'])).total_seconds() <= 120


def initialize(db, now):
    db.executemany('INSERT OR IGNORE INTO paper_accounts VALUES(?,?,?)', [('a', 100000, 100000), ('hk', 100000, 100000)])
    db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('paper_started', now.isoformat()))


def collect_orders(db, now):
    started = stamp(db.execute("SELECT value FROM meta WHERE key='paper_started'").fetchone()[0])
    for result in db.execute('SELECT * FROM model_results ORDER BY timestamp,id').fetchall():
        if stamp(result['timestamp']) < started:
            continue
        data = json.loads(result['data'])
        for stock in data.get('stocks', []):
            code = re.sub(r'^(?:sh|sz|hk)', '', stock.get('code') or '')
            market = 'hk' if re.fullmatch(r'\d{5}', code) else 'a'
            if not re.fullmatch(r'\d{5,6}', code):
                continue
            plan, reason = recommendation_plan(stock, data.get('summary', {}))
            duplicates = sum(re.sub(r'^(?:sh|sz|hk)', '', item.get('code') or '') == code for item in data.get('stocks', []))
            if duplicates > 1:
                plan, reason = None, '同一回复有多条同股计划，需核对'
            if market == 'a' and not code.startswith(('600', '601', '603', '605', '688', '000', '001', '002', '003', '300', '301')):
                plan, reason = None, '当前仅支持沪深A股及港股股票'
            # Keep rejected candidates visible, without ever pretending they are orders.
            order_id = result['user_message_id'] + ':' + market + ':' + code
            db.execute('INSERT OR IGNORE INTO paper_orders(id,request_id,result_id,market,code,name,created,signal_time,status,reason,plan) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (order_id, result['user_message_id'], result['id'], market, code, stock.get('name'), now.isoformat(), result['timestamp'], 'pending' if plan else 'review', reason or '等待行情', json.dumps(plan or {'source': stock}, ensure_ascii=False)))
            if plan and stamp(result['timestamp']).astimezone(LOCAL).date() == now.astimezone(LOCAL).date():
                db.execute("UPDATE paper_orders SET result_id=?,signal_time=?,status='pending',reason='推荐按现价模拟买入，等待行情',plan=? WHERE id=? AND status IN ('review','pending')",
                           (result['id'], result['timestamp'], json.dumps(plan, ensure_ascii=False), order_id))


def process_orders(db, quotes, now):
    for order in db.execute("SELECT * FROM paper_orders WHERE status='pending' ORDER BY signal_time,id").fetchall():
        def update(status, reason):
            db.execute('UPDATE paper_orders SET status=?,reason=? WHERE id=?', (status, reason, order['id']))
        if stamp(order['signal_time']).astimezone(LOCAL).date() != now.astimezone(LOCAL).date():
            update('expired', '建议已跨日，不追溯成交')
            continue
        # A subsequent model reply may revoke the original plan: do not execute an obsolete reply.
        current = db.execute('SELECT id,data FROM model_results WHERE user_message_id=?', (order['request_id'],)).fetchone()
        plan = json.loads(order['plan'])
        stocks = json.loads(current['data']).get('stocks', []) if current else []
        if not current or current['id'] != order['result_id'] or plan['source'] not in stocks:
            update('expired', '原回复已被更新')
            continue
        if not trading(now, order['market']):
            update('pending', '等待连续交易时段')
            continue
        q = quotes.get(symbol(order['market'], order['code']))
        if not fresh(q, now):
            update('pending', '行情缺失或延迟超过120秒')
            continue
        if stamp(q['timestamp']) < stamp(order['signal_time']):
            update('pending', '等待建议发布后的行情')
            continue
        price = q['ask']  # zero ask (suspended/limit-up/no liquidity) must not fill at last price
        if price <= 0 or not math.isfinite(price):
            update('pending', '暂无有效卖一报价')
            continue
        held = db.execute("SELECT 1 FROM paper_orders WHERE market=? AND code=? AND status='filled'", (order['market'], order['code'])).fetchone()
        if held:
            update('skipped', '已有持仓，不重复加仓')
            continue
        cash = db.execute('SELECT cash FROM paper_accounts WHERE market=?', (order['market'],)).fetchone()[0]
        star = order['market'] == 'a' and order['code'].startswith('688')
        minimum = 200 if star else q['lot']
        step = 1 if star else q['lot']
        affordable = int(Decimal(str(cash)) // Decimal(str(price)))
        target = max(minimum, plan['requested_shares'] or minimum)
        shares = min(target, affordable) // step * step
        if shares < minimum:
            update('pending', '可用余额不足一个交易单位')
            continue
        cost = round(shares * price, 2)
        db.execute('UPDATE paper_accounts SET cash=round(cash-?,2) WHERE market=?', (cost, order['market']))
        db.execute("UPDATE paper_orders SET status='filled',reason='模拟成交',shares=?,price=?,filled=?,quote_time=? WHERE id=?", (shares, price, now.isoformat(), q['timestamp'], order['id']))


def process_exits(db, quotes, now):
    for order in db.execute("SELECT * FROM paper_orders WHERE status='filled'").fetchall():
        plan = json.loads(order['plan'])
        if 'exit_levels' not in plan:
            original = db.execute('SELECT text FROM model_events WHERE id=? UNION ALL SELECT text FROM model_results WHERE id=? LIMIT 1',
                                  (order['result_id'], order['result_id'])).fetchone()
            plan['exit_levels'] = exit_levels(plan['source'], order['code'], original[0] if original else '')
            db.execute('UPDATE paper_orders SET plan=? WHERE id=?', (json.dumps(plan, ensure_ascii=False), order['id']))
        market = order['market']
        quote = quotes.get(symbol(market, order['code']))
        if not trading(now, market) or not fresh(quote, now):
            continue
        at = stamp(quote['timestamp'])
        bought = stamp(order['filled'])
        if at <= bought or not trading(at, market):
            continue
        if market == 'a' and at.astimezone(LOCAL).date() <= bought.astimezone(LOCAL).date():
            continue
        bid = quote.get('bid')
        if not bid or not math.isfinite(bid) or bid <= 0:
            continue
        last = quote['price']
        stop, target = plan['exit_levels']['stop'], plan['exit_levels']['target']
        if stop is not None and last <= stop:
            reason, threshold = '止损', stop
        elif target is not None and last >= target:
            reason, threshold = '止盈', target
        else:
            continue
        db.execute('INSERT INTO paper_sales VALUES(?,?,?,?,?,?,?,?)',
                   (order['id'], reason, threshold, last, bid, order['shares'], now.isoformat(), quote['timestamp']))
        db.execute("UPDATE paper_orders SET status='sold' WHERE id=?", (order['id'],))
        db.execute('UPDATE paper_accounts SET cash=round(cash+?,2) WHERE market=?', (round(bid * order['shares'], 2), market))


def tick(path, now=None, provider=fetch_quotes):
    now = now or dt.datetime.now(UTC)
    with closing(connect(path)) as db:
        with db:
            initialize(db, now)
            from app.paper_capital import apply_config
            apply_config(db, os.getenv('STOCK_PAPER_CONFIG_FILE'))
            collect_orders(db, now)
        symbols = sorted({symbol(row['market'], row['code']) for row in db.execute("SELECT market,code FROM paper_orders WHERE status IN ('pending','filled')")})
        positions = db.execute("SELECT value FROM meta WHERE key='positions'").fetchone()
        for holding in json.loads(positions[0]) if positions else []:
            code = re.sub(r'^(?:sh|sz|hk)', '', str(holding.get('code', '')))
            if re.fullmatch(r'\d{5,6}', code):
                symbols.append(symbol('hk' if len(code) == 5 else 'a', code))
        symbols = sorted(set(symbols))
        error = None
        try:
            quotes = provider(symbols, now) if symbols else {}
        except Exception as exc:
            quotes, error = {}, type(exc).__name__
            LOG.warning('Quote retrieval failed (%s)', error)
        with db:
            db.execute('BEGIN IMMEDIATE')
            for sym, q in quotes.items():
                db.execute('INSERT OR REPLACE INTO paper_quotes VALUES(?,?,?,?,?,?)', (sym, q['price'], q['ask'], q['timestamp'], q['lot'], q['fetched']))
            process_orders(db, quotes, now)
            process_exits(db, quotes, now)
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('paper_sync', json.dumps({'time': now.isoformat(), 'error': error})))


def main():
    logging.basicConfig(level=logging.INFO)
    os.umask(0o027)
    while True:
        try:
            tick(os.getenv('STOCK_DB', '/var/lib/stock2027/console.db'))
        except Exception:
            LOG.exception('Paper sync failed')
        time.sleep(30)


if __name__ == '__main__':
    main()
