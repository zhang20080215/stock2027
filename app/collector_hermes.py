"""Incremental, read-only import of Hermes Agent records into the console database."""
import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time

from app.collector import redact
from app.model_results import STRUCTURE_VERSION, completed_selections, structure_model_result
from app.store import connect

LOG = logging.getLogger(__name__)
UTC = dt.timezone.utc
IMPORT_VERSION = 'hermes-1'
SCANNER_SCRIPT = re.compile(r'[\w./-]*(scan|screen|momentum|trinity|mdata)[\w-]*\.py', re.I)


def iso(value):
    try:
        return dt.datetime.fromtimestamp(float(value), UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ''


def parse_ms(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value)).timestamp() * 1000
    except ValueError:
        return None


def status_name(value):
    return {'ok': 'ok', 'success': 'ok', 'completed': 'ok', 'error': 'error', 'failed': 'error', 'skipped': 'skipped'}.get(str(value).lower(), value or 'unknown')


def tool_calls(message):
    raw = message.get('tool_calls')
    if not raw:
        return []
    try:
        calls = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    result = []
    for call in calls if isinstance(calls, list) else []:
        if not isinstance(call, dict):
            continue
        function = call.get('function') if isinstance(call.get('function'), dict) else call
        name = function.get('name') or call.get('name') or 'tool'
        arguments = function.get('arguments', call.get('arguments', {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {'command': arguments}
        if not isinstance(arguments, dict):
            arguments = {}
        result.append({'id': call.get('id') or call.get('call_id') or '', 'name': name, 'arguments': arguments})
    return result


def script_label(name, arguments):
    command = str(arguments.get('command') or arguments.get('cmd') or '')
    found = SCANNER_SCRIPT.findall(command)
    if found:
        return ', '.join(dict.fromkeys(Path(x).name for x in found))[:150]
    for key in ('path', 'file_path', 'pattern', 'query'):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return (name + ' ' + Path(value).name)[:150]
    return name


def result_status(content):
    text = content or ''
    try:
        data = json.loads(text) if text.strip().startswith('{') else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        if data.get('error'):
            return 'failed'
        code = data.get('exit_code', data.get('exitCode'))
        if isinstance(code, int) and code != 0:
            return 'failed'
        return 'completed'
    if re.search(r'(?i)traceback \(most recent call last\)|\berror\b:|command not found', text[:2000]):
        return 'failed'
    return 'completed'


def open_hermes(path):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def stored_changed(db, path, stat):
    old = db.execute('SELECT size,mtime FROM sources WHERE path=?', (str(path),)).fetchone()
    return not old or (old['size'], old['mtime']) != (stat.st_size, stat.st_mtime_ns)


def save_source(db, path, stat):
    db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?)', (str(path), stat.st_size, stat.st_mtime_ns))


def holding_name_codes(positions_path):
    name_codes = {}
    if not positions_path or not Path(positions_path).is_file():
        return name_codes
    try:
        for item in json.loads(Path(positions_path).read_text(encoding='utf-8')):
            if isinstance(item, dict) and item.get('name') and item.get('code'):
                clean_name = re.sub(r'[*\s]', '', str(item['name']))
                name_codes[clean_name] = str(item['code'])
                name_codes[re.sub(r'(科技|半导体|华夏|中国|股份|ETF富国|ETF华夏|ETF)$', '', clean_name)] = str(item['code'])
    except (OSError, ValueError):
        return {}
    return name_codes


def sync(db_path, hermes_home, positions_path=None, max_sessions=400):
    hermes_home = Path(hermes_home)
    state_db = hermes_home / 'state.db'
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = connect(db_path)
    errors = []
    imported = 0

    if not state_db.is_file():
        info = {'last_sync': dt.datetime.now(UTC).isoformat(), 'errors': ['未找到 Hermes state.db'], 'imported': 0, 'interval': int(os.getenv('SYNC_INTERVAL', '30'))}
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('sync', json.dumps(info, ensure_ascii=False)))
        db.commit()
        db.close()
        return info

    version = db.execute("SELECT value FROM meta WHERE key='import_version'").fetchone()
    if not version or version[0] != IMPORT_VERSION:
        for table in ('model_results', 'model_events', 'tasks', 'reports', 'messages', 'sessions'):
            db.execute('DELETE FROM ' + table)
        db.execute("DELETE FROM meta WHERE key LIKE 'session:%'")
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('import_version', IMPORT_VERSION))
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('model_structure_version', str(STRUCTURE_VERSION)))
        db.commit()

    try:
        source = open_hermes(state_db)
    except sqlite3.Error as exc:
        info = {'last_sync': dt.datetime.now(UTC).isoformat(), 'errors': ['状态库不可读取: ' + type(exc).__name__], 'imported': 0, 'interval': int(os.getenv('SYNC_INTERVAL', '30'))}
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('sync', json.dumps(info, ensure_ascii=False)))
        db.commit()
        db.close()
        return info

    try:
        sessions = [dict(row) for row in source.execute(
            'SELECT id,source,model,started_at,ended_at,message_count,title,last_activity_at FROM sessions ORDER BY last_activity_at DESC LIMIT ?',
            (max_sessions,))]
        ids = [s['id'] for s in sessions]
        marks = ','.join('?' * len(ids)) or 'NULL'
        messages = [dict(row) for row in source.execute(
            'SELECT id,session_id,role,content,tool_calls,tool_name,tool_call_id,timestamp FROM messages WHERE session_id IN (' + marks + ') AND active=1 ORDER BY session_id,timestamp,id',
            ids)] if ids else []
    finally:
        source.close()

    by_session = {}
    for message in messages:
        by_session.setdefault(message['session_id'], []).append(message)

    name_codes = holding_name_codes(positions_path)

    for session in sessions:
        sid = session['id']
        stat = json.dumps([session.get('message_count') or 0, session.get('last_activity_at') or 0])
        old = db.execute('SELECT value FROM meta WHERE key=?', ('session:' + sid,)).fetchone()
        if old and old[0] == stat:
            continue
        rows = by_session.get(sid, [])
        title = (session.get('title') or '').strip()
        last_user_prompt = ''
        updated = iso(session.get('last_activity_at') or session.get('ended_at') or session.get('started_at'))
        with db:
            db.execute('DELETE FROM messages WHERE session_id=?', (sid,))
            db.execute('DELETE FROM tasks WHERE session_id=?', (sid,))
            for message in rows:
                role = message.get('role')
                content = message.get('content') or ''
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                stamp = iso(message.get('timestamp'))
                if role == 'user':
                    last_user_prompt = text
                    if text.strip():
                        db.execute('INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?)', (sid + ':' + str(message['id']), sid, 'user', stamp, redact(text)))
                elif role == 'assistant':
                    for call in tool_calls(message):
                        task_id = sid + ':' + str(call['id'] or message['id'])
                        db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?,?)', (task_id, sid, call['name'], script_label(call['name'], call['arguments']), stamp, None, 'unconfirmed'))
                    if text.strip():
                        db.execute('INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?)', (sid + ':' + str(message['id']), sid, 'assistant', stamp, redact(text)))
                elif role == 'tool':
                    content_text = text
                    try:
                        data = json.loads(text)
                        if isinstance(data, dict):
                            content_text = data.get('output') or data.get('content') or text
                            if isinstance(content_text, (dict, list)):
                                content_text = json.dumps(content_text, ensure_ascii=False)
                    except (ValueError, TypeError):
                        pass
                    state = result_status(text)
                    task_id = sid + ':' + str(message.get('tool_call_id') or '')
                    db.execute('UPDATE tasks SET finished=?,status=? WHERE id=?', (stamp, state, task_id))
                    task = db.execute('SELECT label FROM tasks WHERE id=?', (task_id,)).fetchone()
                    if task and SCANNER_SCRIPT.search(task['label']) and content_text.strip():
                        db.execute('INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?)', (sid + ':' + str(message['id']), sid, stamp, task['label'], redact(content_text[:200000])))

            proxy_rows = [{
                'id': message['id'],
                'type': 'message',
                'timestamp': message.get('timestamp'),
                'message': {
                    'role': message.get('role'),
                    'content': message.get('content'),
                    'stopReason': 'tool_calls' if tool_calls(message) else 'stop',
                    'toolCallId': message.get('tool_call_id'),
                    'provider': session.get('source'),
                    'model': session.get('model'),
                },
            } for message in rows]
            for result in completed_selections(proxy_rows, sid, SCANNER_SCRIPT, keep_all=True):
                safe_text, safe_prompt = redact(result['text']), redact(result['prompt'])
                if safe_prompt.lstrip().startswith('[IMPORTANT') and title:
                    safe_prompt = re.sub(r'\s+·\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}$', '', title)
                data = structure_model_result(safe_text, safe_prompt, name_codes)
                data['report_ids'] = result['report_ids']
                stamp = iso(result['timestamp'])
                db.execute('INSERT OR REPLACE INTO model_events VALUES(?,?,?,?,?,?,?,?,?)', (result['id'], sid, result['user_message_id'], safe_prompt, stamp, session.get('source') or '', session.get('model') or '', safe_text, json.dumps(data, ensure_ascii=False)))
                db.execute('INSERT INTO model_results VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_message_id) DO UPDATE SET id=excluded.id,prompt=excluded.prompt,timestamp=excluded.timestamp,provider=excluded.provider,model=excluded.model,text=excluded.text,data=excluded.data WHERE excluded.timestamp>=model_results.timestamp', (result['id'], sid, result['user_message_id'], safe_prompt, stamp, session.get('source') or '', session.get('model') or '', safe_text, json.dumps(data, ensure_ascii=False)))

            db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?,?)', (sid, session.get('source') or 'unknown', title or last_user_prompt.replace('\n', ' ')[:72] or '历史会话', updated))
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('session:' + sid, stat))
        imported += 1

    note_paths = []
    for name in ('MEMORY.md', 'USER.md'):
        candidate = hermes_home / 'memories' / name
        if candidate.is_file():
            note_paths.append((candidate, '长期记忆' if name == 'MEMORY.md' else '用户画像'))
    plans = hermes_home / 'plans'
    if plans.is_dir():
        for path in sorted(plans.glob('*.md')):
            note_paths.append((path, {'pending-orders.md': '开盘执行台账', 'short-term-ledger.md': '短线交易台账', 'catalyst-calendar.md': '催化剂日历'}.get(path.name, path.stem)))
    for path, doc_title in note_paths:
        try:
            stat = path.stat()
            if not stored_changed(db, path, stat):
                continue
            with db:
                db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?)', (path.name, doc_title, iso(stat.st_mtime), redact(path.read_text(encoding='utf-8', errors='replace'))))
                save_source(db, path, stat)
            imported += 1
        except (OSError, UnicodeError) as exc:
            errors.append(path.name + ': ' + type(exc).__name__)

    rules = []
    rule_paths = [hermes_home / name for name in ('AGENTS.md', 'SOUL.md', 'IDENTITY.md', 'HEARTBEAT.md')]
    skills = hermes_home / 'skills'
    if skills.is_dir():
        for path in sorted(skills.rglob('SKILL.md')):
            if set(path.parts) & {'.archive', '.curator_backups', '.hub', '.locks'}:
                continue
            rule_paths.append(path)
    for path in rule_paths:
        if not path.is_file():
            continue
        try:
            rules.append({'id': str(path.relative_to(hermes_home)).replace('\\', '/'),
                          'title': path.parent.name if path.name == 'SKILL.md' else path.stem,
                          'timestamp': iso(path.stat().st_mtime),
                          'text': redact(path.read_text(encoding='utf-8', errors='replace'))})
        except (OSError, UnicodeError) as exc:
            errors.append(path.name + ': ' + type(exc).__name__)
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('rules', json.dumps(rules, ensure_ascii=False)))

    scheduler = {'status': 'unknown', 'items': [], 'note': '未找到调度数据'}
    jobs_file = hermes_home / 'cron' / 'jobs.json'
    if jobs_file.is_file():
        try:
            jobs = json.loads(jobs_file.read_text(encoding='utf-8')).get('jobs', [])
            items = []
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                items.append({'id': job.get('id'), 'name': job.get('name'), 'enabled': job.get('enabled'),
                              'schedule': job.get('schedule'),
                              'state': {'lastRunAtMs': parse_ms(job.get('last_run_at')), 'nextRunAtMs': parse_ms(job.get('next_run_at')),
                                        'lastStatus': status_name(job.get('last_status')), 'lastError': job.get('last_error') or job.get('last_delivery_error'),
                                        'consecutiveErrors': 0}})
            scheduler = {'status': 'available', 'items': items, 'note': '来自 Hermes cron/jobs.json；运行状态以调度器记录为准'}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            scheduler['note'] = '调度数据不可读取：' + type(exc).__name__
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('scheduler', redact(json.dumps(scheduler, ensure_ascii=False))))

    if positions_path and Path(positions_path).is_file():
        try:
            path = Path(positions_path)
            positions = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(positions, list):
                raise ValueError('Expected a position list')
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('positions', json.dumps(positions, ensure_ascii=False)))
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('positions_updated', iso(path.stat().st_mtime)))
        except (OSError, ValueError) as exc:
            errors.append('positions.json: ' + type(exc).__name__)

    info = {'last_sync': dt.datetime.now(UTC).isoformat(), 'errors': errors, 'imported': imported, 'interval': int(os.getenv('SYNC_INTERVAL', '30'))}
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('sync', json.dumps(info, ensure_ascii=False)))
    db.commit()
    db.close()
    return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    os.umask(0o027)
    db_path = os.getenv('STOCK_DB', '/var/lib/stock2027/console.db')
    home = os.getenv('STOCK_HERMES_HOME', os.getenv('HERMES_HOME', str(Path.home() / '.hermes')))
    positions = os.getenv('STOCK_POSITIONS', os.getenv('HERMES_POSITIONS'))
    while True:
        try:
            info = sync(db_path, home, positions)
            LOG.info('Sync complete: %d updated items; %d errors', info['imported'], len(info['errors']))
        except Exception:
            LOG.exception('Sync failed')
            if args.once:
                raise
        if args.once:
            return
        time.sleep(max(5, int(os.getenv('SYNC_INTERVAL', '30'))))


if __name__ == '__main__':
    main()
