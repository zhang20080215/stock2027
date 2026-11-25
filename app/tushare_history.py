"""Fetch unadjusted historical minute bars via Tushare HTTPS; never log tokens."""
import argparse
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen

from app.paper import LOCAL, plan_for, stamp
from app.store import connect


class RateLimitError(RuntimeError):
    pass


def read_token(path):
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        match = re.match(r'^\s*TUSHARE_TOKEN\s*=\s*(.*?)\s*$', line)
        if match:
            value = match[1].strip('"\'')
            if value:
                return value
    raise ValueError('TUSHARE_TOKEN is missing')


def request_minutes(token, code, day, end_day=None):
    payload = {'api_name': 'stk_mins', 'token': token, 'params': {'ts_code': code + ('.SH' if code.startswith('6') else '.SZ'), 'freq': '1min', 'start_date': day + ' 09:00:00', 'end_date': (end_day or day) + ' 15:30:00'}, 'fields': 'ts_code,trade_time,close,vol'}
    req = Request('https://api.tushare.pro', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=30) as response:
        result = json.load(response)
    if result.get('code') != 0:
        # Provider messages can echo request details: log only the numeric code.
        if '频率' in str(result.get('msg', '')):
            raise RateLimitError('Tushare minute rate limit')
        raise RuntimeError('Tushare error code ' + str(result.get('code')))
    data = result.get('data') or {}
    items = data.get('items') or []
    if len(items) >= 8000:
        raise RuntimeError('Minute response may be truncated')
    return [dict(zip(data['fields'], item)) for item in items]


def targets(db, start, end):
    result = {}
    for row in db.execute('SELECT timestamp,data FROM model_events ORDER BY timestamp'):
        when = stamp(row['timestamp']).astimezone(LOCAL)
        if not start <= when.date() <= end:
            continue
        data = json.loads(row['data'])
        for stock in data.get('stocks', []):
            code = re.sub(r'^(?:sh|sz|hk)', '', stock.get('code') or '')
            plan, _ = plan_for(dict(stock, entry=(stock.get('entry') or '').replace('附近', '')), data.get('summary', {}))
            if plan and re.fullmatch(r'(?:60\d{4}|688\d{3}|00\d{4}|30\d{4})', code):
                result[code] = min(result.get(code, when.date()), when.date())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--start', default='2026-11-01')
    parser.add_argument('--end', default='2026-11-24')
    parser.add_argument('--request-interval', type=float, default=65)
    args = parser.parse_args()
    token = read_token(args.env_file)
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    total, calls = 0, 0
    last_request = time.monotonic()
    with closing(connect(args.db)) as db:
        for code, first in targets(db, start, end).items():
            day = first
            while day <= end:
                if day.weekday() < 5:
                    stop = min(end, day + dt.timedelta(days=13))
                    time.sleep(max(0, max(65, args.request_interval) - (time.monotonic() - last_request)))
                    for attempt in range(3):
                        try:
                            rows = request_minutes(token, code, day.isoformat(), stop.isoformat())
                            break
                        except RateLimitError:
                            print(json.dumps({'status': 'rate_limited', 'attempt': attempt + 1}), flush=True)
                            if attempt == 2:
                                raise
                            time.sleep(65)
                    last_request = time.monotonic()
                    with db:
                        for row in rows:
                            when = dt.datetime.fromisoformat(row['trade_time']).replace(tzinfo=LOCAL)
                            if not day <= when.date() <= stop or str(row['ts_code']).split('.')[0] != code:
                                raise ValueError('Unexpected minute response')
                            db.execute('INSERT OR REPLACE INTO history_bars VALUES(?,?,?,?,?,?)', ('tushare', 'a', code, when.isoformat(), float(row['close']), float(row.get('vol') or 0)))
                    total += len(rows)
                    calls += 1
                    print(json.dumps({'code': code, 'start': day.isoformat(), 'end': stop.isoformat(), 'rows': len(rows)}), flush=True)
                    day = stop
                day += dt.timedelta(days=1)
        with db:
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('tushare_history', json.dumps({'rows': total, 'calls': calls, 'start': args.start, 'end': args.end, 'hk_permission': False})))


if __name__ == '__main__':
    main()
