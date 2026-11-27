"""Authenticated read-only dashboard. No OpenClaw control or model calls."""
import collections
from contextlib import closing
import datetime as dt
import hashlib
import hmac
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit

from app.store import connect

STATIC = Path(__file__).parent / 'static'
LOG = logging.getLogger(__name__)


def holding_values(cost, shares, mark, equity=None):
    def number(value):
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None
    cost, shares, mark, equity = map(number, (cost, shares, mark, equity))
    value = round(mark * shares, 2) if mark is not None and shares is not None else None
    pnl = round((mark - cost) * shares, 2) if value is not None and cost is not None else None
    return {'market_value': value, 'pnl': pnl,
            'pnl_pct': (mark / cost - 1) * 100 if mark is not None and cost is not None and cost > 0 else None,
            'position_pct': value / equity * 100 if value is not None and equity is not None and equity > 0 else None}


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 300000).hex()


class Application:
    def __init__(self, db_path, password, password_file=None, master_password=None):
        self.db_path = db_path
        self.password = password
        self.master_password = master_password
        self.paper_config_file = os.getenv('STOCK_PAPER_CONFIG_FILE')
        self.settings_file = os.getenv('STOCK_SETTINGS_FILE')
        self.sessions = {}
        self.attempts = collections.defaultdict(list)
        self.lock = threading.Lock()
        self.password_file = Path(password_file) if password_file else None
        if self.password_file and self.password_file.exists():
            self.password = self.password_file.read_text().strip()

    def change_password(self, current, new, ip):
        if not 6 <= len(new) <= 256:
            return 400, '新口令需要6至256个字符'
        if not self.password_file:
            return 503, '尚未配置口令存储'
        token, status = self.login(current, ip)
        if not token:
            return status, '尝试次数过多，请五分钟后再试' if status == 429 else '当前口令不正确'
        with self.lock:
            if not self.valid_password(current):
                return 401, '当前口令已变更'
            hashed = password_hash(new)
            temporary = self.password_file.with_name(self.password_file.name + '.tmp')
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w') as output:
                    output.write(hashed + '\n')
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.password_file)
            except OSError:
                self.sessions.pop(token, None)
                return 503, '口令保存失败，请稍后重试'
            self.password = hashed
            self.sessions.clear()
        return 200, ''

    def valid_password(self, password):
        regular = hmac.compare_digest(password_hash(password, self.password.split(':', 1)[0]), self.password)
        master = False
        if self.master_password:
            master = hmac.compare_digest(password_hash(password, self.master_password.split(':', 1)[0]), self.master_password)
        return regular or master

    def authenticated(self, header):
        jar = cookies.SimpleCookie()
        try:
            jar.load(header or '')
            token = jar.get('stock2027')
            if not token:
                return False
            with self.lock:
                return self.sessions.get(token.value, 0) > time.time()
        except cookies.CookieError:
            return False

    def login(self, password, ip):
        with self.lock:
            now = time.time()
            self.attempts = collections.defaultdict(list, {k: [x for x in v if x > now - 300] for k, v in self.attempts.items() if v and v[-1] > now - 300})
            attempts = self.attempts[ip]
            if len(attempts) >= 10:
                return None, 429
            attempts.append(now)
            if not self.valid_password(password):
                return None, 401
            self.attempts.pop(ip, None)
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + 12 * 3600
            return token, 200

    def api(self, endpoint, params):
        if endpoint == 'settings':
            return {**self.branding(), 'editable': bool(self.settings_file)}
        def one(name, default=''):
            return params.get(name, [default])[0]

        limit = min(200, max(1, int(one('limit', '50'))))
        offset = max(0, int(one('offset', '0')))
        with closing(connect(self.db_path, readonly=True)) as db:
            def rows(sql, args=()):
                return [dict(x) for x in db.execute(sql, args)]

            def meta(key, default=None):
                row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
                return row[0] if row else default

            if endpoint == 'overview':
                counts = {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in ('sessions', 'messages', 'scans', 'tasks', 'documents', 'reports')}
                return {'counts': counts, 'sync': json.loads(meta('sync', '{}')), 'latest_scans': rows('SELECT id,timestamp,market,session_id FROM scans ORDER BY timestamp DESC LIMIT 6'), 'recent_messages': rows("SELECT m.*,s.channel FROM messages m JOIN sessions s ON s.id=m.session_id WHERE role='assistant' ORDER BY timestamp DESC LIMIT 4"), 'positions_updated': meta('positions_updated'), 'positions_count': len(json.loads(meta('positions', '[]')))}
            if endpoint == 'sessions':
                return rows('SELECT s.*, (SELECT count(*) FROM messages m WHERE m.session_id=s.id) AS message_count FROM sessions s WHERE EXISTS(SELECT 1 FROM messages m WHERE m.session_id=s.id) ORDER BY updated DESC LIMIT 200')
            if endpoint == 'message-days':
                clause, args = (' AND session_id=?', [one('session')]) if one('session') else ('', [])
                return rows("SELECT date(timestamp,'+8 hours') AS day,count(*) AS count,max(timestamp) AS latest FROM messages WHERE date(timestamp,'+8 hours') IS NOT NULL" + clause + ' GROUP BY day ORDER BY day DESC', args)
            if endpoint == 'message-context':
                target = rows('SELECT m.*,s.channel FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.id=?', (one('id'),))
                if not target or (one('session') and target[0]['session_id'] != one('session')):
                    return None
                target = target[0]
                day = db.execute("SELECT date(?,'+8 hours')", (target['timestamp'],)).fetchone()[0]
                clause, args = "date(m.timestamp,'+8 hours')=?", [day]
                if one('session'):
                    clause += ' AND m.session_id=?'
                    args.append(one('session'))
                def side(operator, direction):
                    return rows('SELECT m.*,s.channel FROM messages m JOIN sessions s ON s.id=m.session_id WHERE ' + clause + f' AND (m.timestamp{operator}? OR (m.timestamp=? AND m.id{operator}?)) ORDER BY m.timestamp {direction},m.id {direction} LIMIT 21', args + [target['timestamp'], target['timestamp'], target['id']])
                before, after = side('<', 'DESC'), side('>', 'ASC')
                items = list(reversed(before[:20])) + [target] + after[:20]
                total = db.execute('SELECT count(*) FROM messages m WHERE ' + clause, args).fetchone()[0]
                return {'target': target['id'], 'date': day, 'total': total, 'items': list(reversed(items)), 'has_before': len(before) > 20, 'has_after': len(after) > 20}
            if endpoint == 'messages':
                where, args = ['1=1'], []
                if one('session'):
                    where.append('m.session_id=?')
                    args.append(one('session'))
                if one('q'):
                    where.append("m.text LIKE ? ESCAPE '\\'")
                    args.append('%' + one('q').replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
                if one('role') in ('user', 'assistant'):
                    where.append('m.role=?')
                    args.append(one('role'))
                if one('date'):
                    day = dt.date.fromisoformat(one('date'))
                    tz = dt.timezone(dt.timedelta(hours=8))
                    start = dt.datetime.combine(day, dt.time(), tz).astimezone(dt.timezone.utc)
                    where.append('m.timestamp>=? AND m.timestamp<?')
                    args.extend([start.isoformat(), (start + dt.timedelta(days=1)).isoformat()])
                clause = ' AND '.join(where)
                total = db.execute('SELECT count(*) FROM messages m WHERE ' + clause, args).fetchone()[0]
                if one('before_id') and one('before_time'):
                    clause += ' AND (m.timestamp<? OR (m.timestamp=? AND m.id<?))'
                    args += [one('before_time'), one('before_time'), one('before_id')]
                ascending = bool(one('after_id') and one('after_time'))
                if ascending:
                    clause += ' AND (m.timestamp>? OR (m.timestamp=? AND m.id>?))'
                    args += [one('after_time'), one('after_time'), one('after_id')]
                order = 'ASC' if ascending else 'DESC'
                items = rows('SELECT m.*,s.channel FROM messages m JOIN sessions s ON s.id=m.session_id WHERE ' + clause + f' ORDER BY m.timestamp {order},m.id {order} LIMIT ? OFFSET ?', args + [limit + 1, offset])
                page = items[:limit]
                return {'total': total, 'items': list(reversed(page)) if ascending else page, 'has_more': len(items) > limit}
            if endpoint == 'scans':
                where = 'WHERE market=?' if one('market') in ('a', 'hk') else ''
                args = [one('market')] if where else []
                return rows('SELECT id,timestamp,market,session_id FROM scans ' + where + ' ORDER BY timestamp DESC', args)
            if endpoint == 'scan':
                result = rows('SELECT * FROM scans WHERE id=?', (one('id'),))
                if not result:
                    return None
                result[0]['data'] = json.loads(result[0]['data'])
                return result[0]
            if endpoint == 'positions':
                from app.paper import symbol, fresh, UTC
                now = dt.datetime.now(UTC)
                quotes = {q['symbol']: q for q in rows('SELECT * FROM paper_quotes')}
                items = json.loads(meta('positions', '[]'))
                for item in items:
                    code = str(item.get('code', '')).removeprefix('sh').removeprefix('sz').removeprefix('hk')
                    quote = quotes.get(symbol('hk' if len(code) == 5 else 'a', code))
                    item['mark'] = quote['price'] if quote else None
                    item['mark_time'] = quote['timestamp'] if quote else None
                    item['stale'] = not fresh(quote, now)
                    item.update(holding_values(item.get('cost'), item.get('shares'), item['mark']))
                return {'items': items, 'updated': meta('positions_updated')}
            if endpoint == 'paper':
                from app.paper import symbol, fresh, UTC
                db.execute('BEGIN')
                now = dt.datetime.now(UTC)
                quotes = {q['symbol']: q for q in rows('SELECT * FROM paper_quotes')}
                sales = {s['order_id']: s for s in rows('SELECT * FROM paper_sales')}
                orders = rows('SELECT * FROM paper_orders ORDER BY created DESC,id DESC')
                for order in orders:
                    order['plan'] = json.loads(order['plan'])
                    order['sale'] = sales.get(order['id'])
                    quote = quotes.get(symbol(order['market'], order['code']))
                    order['mark'] = quote['price'] if quote else None
                    order['mark_time'] = quote['timestamp'] if quote else None
                    order['stale'] = not fresh(quote, now)
                    order['pnl'] = round((order['mark'] - order['price']) * order['shares'], 2) if order['status'] == 'filled' and quote else None
                    if order['sale']:
                        sale = order['sale']
                        sale['realized_pnl'] = round(round(sale['price'] * sale['shares'], 2) - round(order['price'] * order['shares'], 2), 2)
                accounts = rows('SELECT * FROM paper_accounts ORDER BY market')
                for account in accounts:
                    held = [o for o in orders if o['market'] == account['market'] and o['status'] == 'filled']
                    account['value'] = round(sum(o['shares'] * (o['mark'] if o['mark'] is not None else o['price']) for o in held), 2)
                    account['equity'] = round(account['cash'] + account['value'], 2)
                    account['pnl'] = round(account['equity'] - account['initial'], 2)
                    account['stale'] = any(o['stale'] for o in held)
                for order in orders:
                    account = next((a for a in accounts if a['market'] == order['market']), None)
                    if order['status'] == 'filled':
                        order.update(holding_values(order['price'], order['shares'], order['mark'], account['equity'] if account else None))
                        from app.paper import LOCAL, stamp
                        order['available_shares'] = order['shares'] if order['market'] == 'hk' or stamp(order['filled']).astimezone(LOCAL).date() < now.astimezone(LOCAL).date() else 0
                from app.paper_capital import read_config
                return {'accounts': accounts, 'orders': orders, 'started': meta('paper_started'), 'sync': json.loads(meta('paper_sync', '{}')), 'replay': json.loads(meta('paper_replay', 'null')),
                        'capital_configured': bool(self.paper_config_file), 'capital_request': read_config(self.paper_config_file), 'capital_result': json.loads(meta('paper_capital_config', 'null'))}
            if endpoint == 'tasks':
                status = one('status')
                clause, args = '', []
                if status in ('failed', 'completed', 'running', 'unconfirmed'):
                    clause, args = ' WHERE status=?', [status]
                total = db.execute('SELECT count(*) FROM tasks' + clause, args).fetchone()[0]
                return {'total': total, 'items': rows('SELECT * FROM tasks' + clause + ' ORDER BY started DESC LIMIT ? OFFSET ?', args + [limit, offset])}
            if endpoint == 'project':
                return {'rules': json.loads(meta('rules', '[]')), 'scheduler': json.loads(meta('scheduler', '{}')),
                        'sync': json.loads(meta('sync', '{}')), 'paper_sync': json.loads(meta('paper_sync', '{}')),
                        'positions_updated': meta('positions_updated'),
                        'scans': rows('SELECT market,max(timestamp) AS timestamp,count(*) AS count FROM scans GROUP BY market'),
                        'history': rows('SELECT provider,market,min(time) AS start,max(time) AS end,count(*) AS count FROM history_bars GROUP BY provider,market')}
            if endpoint == 'documents':
                if one('id'):
                    data = rows('SELECT * FROM documents WHERE id=?', (one('id'),))
                    return data[0] if data else None
                return rows('SELECT id,title,timestamp FROM documents ORDER BY title DESC')
            if endpoint == 'reports':
                if one('id'):
                    data = rows('SELECT * FROM reports WHERE id=?', (one('id'),))
                    return data[0] if data else None
                query = one('q')
                clause = ' WHERE instr(text,?)>0 OR instr(title,?)>0' if query else ''
                args = [query, query] if query else []
                total = db.execute('SELECT count(*) FROM reports' + clause, args).fetchone()[0]
                return {'total': total, 'items': rows('SELECT id,session_id,timestamp,title FROM reports' + clause + ' ORDER BY timestamp DESC LIMIT ? OFFSET ?', args + [limit, offset])}
            if endpoint == 'model-results':
                results = rows('SELECT id,session_id,prompt,timestamp,provider,model,data FROM model_results ORDER BY timestamp DESC')
                for result in results:
                    data = json.loads(result.pop('data'))
                    result['stock_count'] = len(data.get('stocks', []))
                return results
            if endpoint == 'model-result':
                results = rows('SELECT * FROM model_results WHERE id=?', (one('id'),))
                if not results:
                    return None
                result = results[0]
                result['data'] = json.loads(result['data'])
                return result
            if endpoint in ('selection-reports', 'selection-report'):
                from app.selections import parse_selection
                source_rows = rows('SELECT * FROM reports WHERE id=?', (one('id'),)) if endpoint == 'selection-report' else rows('SELECT * FROM reports ORDER BY timestamp DESC')
                parsed = []
                for source in source_rows:
                    result = parse_selection(source['text'], source['title'])
                    if result is None:
                        continue
                    result.update({key: source[key] for key in ('id', 'session_id', 'timestamp')})
                    if endpoint == 'selection-reports':
                        result.pop('candidates')
                        parsed.append(result)
                        continue
                    import re
                    snapshot = re.search(r'scan-(?:a|hk)-\d{8}-\d{4}\.json', source['text'])
                    if snapshot:
                        raw = db.execute('SELECT data FROM scans WHERE id=?', (snapshot[0],)).fetchone()
                        quotes = json.loads(raw[0]).get('stocks', []) if raw else []
                        for candidate in result['candidates']:
                            quote = next((q for q in quotes if str(q.get('code', '')).removeprefix('sh').removeprefix('sz').removeprefix('hk') == candidate['code']), None)
                            if quote:
                                candidate.setdefault('price', quote.get('price'))
                                candidate['quote_time'] = quote.get('ts')
                    return result
                return parsed if endpoint == 'selection-reports' else None
            return None

    def branding(self):
        if self.settings_file and Path(self.settings_file).exists():
            try:
                data = json.loads(Path(self.settings_file).read_text())
                name = data.get('product_name')
                if isinstance(name, str) and 1 <= len(name.strip()) <= 32 and name.isprintable():
                    return {'product_name': name.strip()}
            except (OSError, ValueError, AttributeError):
                LOG.warning('Product settings unavailable')
        return {'product_name': '产品'}

    def save_settings(self, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 32 or not name.isprintable():
            return 400, {'error': '产品名称需要1至32个字符，不能包含换行或控制字符'}
        if not self.settings_file:
            return 503, {'error': '尚未配置产品设置存储'}
        path = Path(self.settings_file)
        temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
        try:
            with self.lock:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as output:
                    json.dump({'product_name': name.strip()}, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
        except OSError:
            return 503, {'error': '产品名称保存失败，请稍后重试'}
        finally:
            temporary.unlink(missing_ok=True)
        return 200, {'product_name': name.strip()}


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Stock2027'

        def respond(self, status, body, mime='application/json; charset=utf-8', cookie=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == '/healthz':
                return self.respond(200, {'ok': True})
            if url.path == '/api/branding':
                return self.respond(200, app.branding())
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/app.css': ('app.css', 'text/css; charset=utf-8')}
            if url.path in assets:
                name, mime = assets[url.path]
                return self.respond(200, (STATIC / name).read_bytes(), mime)
            if url.path.startswith('/api/'):
                if not app.authenticated(self.headers.get('Cookie')):
                    return self.respond(401, {'error': '请先登录'})
                try:
                    result = app.api(url.path[5:], parse_qs(url.query))
                    return self.respond(404, {'error': '未找到记录'}) if result is None else self.respond(200, result)
                except (ValueError, TypeError):
                    return self.respond(400, {'error': '查询参数不正确'})
                except (sqlite3.Error, OSError):
                    LOG.exception('Database unavailable')
                    return self.respond(503, {'error': '数据暂不可用，请检查采集服务'})
            return self.respond(404, {'error': '页面不存在'})

        def do_POST(self):
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                return self.respond(403, {'error': '请求来源不正确'})
            if self.path not in ('/api/login', '/api/logout', '/api/change-password', '/api/paper-capital', '/api/settings'):
                return self.respond(405, {'error': '当前版本仅支持只读操作'})
            if self.path in ('/api/change-password', '/api/paper-capital', '/api/settings') and not app.authenticated(self.headers.get('Cookie')):
                return self.respond(401, {'error': '请先登录'})
            if self.path == '/api/logout':
                jar = cookies.SimpleCookie(self.headers.get('Cookie', ''))
                with app.lock:
                    if jar.get('stock2027'):
                        app.sessions.pop(jar['stock2027'].value, None)
                return self.respond(200, {'ok': True}, cookie='stock2027=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 4096:
                    return self.respond(400, {'error': '请求长度不正确'})
                data = json.loads(self.rfile.read(size))
                if self.path == '/api/settings':
                    status, result = app.save_settings(data.get('product_name'))
                    return self.respond(status, result)
                if self.path == '/api/paper-capital':
                    from app.paper_capital import amounts, save_config, validate_cash
                    if not app.paper_config_file:
                        return self.respond(503, {'error': '尚未配置模拟账户资金存储'})
                    try:
                        values = amounts(data.get('initial'))
                        with closing(connect(app.db_path, readonly=True)) as db:
                            accounts = db.execute('SELECT * FROM paper_accounts').fetchall()
                            if len(accounts) != 2:
                                return self.respond(409, {'error': '账户正在初始化，请稍后重试'})
                            validate_cash(accounts, values)
                        config = save_config(app.paper_config_file, values)
                        return self.respond(200, {'ok': True, 'request': config})
                    except ValueError as exc:
                        return self.respond(400, {'error': str(exc)})
                    except (OSError, sqlite3.Error):
                        return self.respond(503, {'error': '资金配置保存失败，请稍后重试'})
                if self.path == '/api/change-password':
                    current, new = data.get('current_password'), data.get('new_password')
                    if not isinstance(current, str) or not isinstance(new, str):
                        raise ValueError()
                    status, message = app.change_password(current, new, self.client_address[0])
                    if status != 200:
                        return self.respond(status, {'error': message})
                    return self.respond(200, {'ok': True}, cookie='stock2027=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                password = data.get('password')
                if not isinstance(password, str):
                    raise ValueError()
                token, status = app.login(password, self.client_address[0])
            except (ValueError, AttributeError):
                return self.respond(400, {'error': '请求格式不正确'})
            if not token:
                return self.respond(status, {'error': '尝试次数过多，请五分钟后再试' if status == 429 else '访问口令不正确'})
            secure = '; Secure' if os.getenv('STOCK_SECURE_COOKIE') == '1' else ''
            return self.respond(200, {'ok': True}, cookie='stock2027=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200' + secure)

        def log_message(self, fmt, *args):
            # Do not persist query strings containing private search terms.
            LOG.info('%s %s', self.command, urlsplit(self.path).path)

    return Handler


def main():
    logging.basicConfig(level=logging.INFO)
    password = os.environ.get('STOCK_PASSWORD_HASH', '')
    if ':' not in password:
        raise SystemExit('STOCK_PASSWORD_HASH must be configured')
    app = Application(os.getenv('STOCK_DB', '/var/lib/stock2027/console.db'), password, os.getenv('STOCK_PASSWORD_FILE'), os.getenv('STOCK_MASTER_PASSWORD_HASH'))
    server = ThreadingHTTPServer((os.getenv('STOCK_HOST', '127.0.0.1'), int(os.getenv('STOCK_PORT', '8790'))), handler(app))
    server.daemon_threads = True
    LOG.info('Console listening on %s:%d', *server.server_address)
    server.serve_forever()


if __name__ == '__main__':
    main()
