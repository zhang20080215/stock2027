"""Incremental, read-only OpenClaw import. Never executes source files or tools."""
import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path
import re
import time

from app.store import connect
from app.model_results import completed_selections, structure_model_result, STRUCTURE_VERSION

LOG = logging.getLogger(__name__)
UTC = dt.timezone.utc
IMPORT_VERSION = '4'
SCANNER_INVOCATION = re.compile(r'''(?:^|[\s/"'])python(?:3(?:\.\d+)?)?["']?\s+(?:-u\s+)?["']?(?:[^\s"';&|]*/)?(a-scan\.py|hk-scan\.py|yaogu-scan\.py|screen_ashare\.py|trinity\.py)(?=["'\s;&|]|$)''')


def redact(text):
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)', '[私钥已隐藏]', text, flags=re.S)
    text = re.sub(r'(?i)\b(?:sk-[\w-]{10,}|gh[pousr]_[\w]{15,}|github_pat_[\w]+)', '[密钥已隐藏]', text)
    text = re.sub(r'(?i)((?:[\w-]*(?:api[_-]?key|token|password|secret)[\w-]*)[\s"\x27]*[:=]\s*)[^\s,;\n]+', r'\1[已隐藏]', text)
    text = re.sub(r'(?i)(authorization\s*:\s*bearer\s+)\S+', r'\1[已隐藏]', text)
    return text


def plain(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(x.get('text', '') for x in content if isinstance(x, dict) and x.get('type') == 'text')
    return ''


def timestamp(value, fallback=''):
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value / 1000, UTC).isoformat()
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC).isoformat()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return fallback


def changed(db, path):
    stat = path.stat()
    old = db.execute('SELECT size,mtime FROM sources WHERE path=?', (str(path),)).fetchone()
    return not old or (old['size'], old['mtime']) != (stat.st_size, stat.st_mtime_ns)


def save_source(db, path, stat):
    db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?)', (str(path), stat.st_size, stat.st_mtime_ns))


def session_channels(session_dir):
    result = {}
    index = session_dir / 'sessions.json'
    if index.exists():
        for key, value in json.loads(index.read_text()).items():
            channel = value.get('deliveryContext', {}).get('channel') or value.get('origin', {}).get('provider') or 'unknown'
            for sid in [value.get('sessionId')] + value.get('usageFamilySessionIds', []):
                if sid:
                    result[sid] = channel
    return result


def import_session(db, path, channels):
    """Reparse changed transcript atomically; reset archives share stable event IDs."""
    stat = path.stat()
    sid = path.name.split('.jsonl')[0]
    rows = []
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # An appended incomplete line is retried on the next poll.
                continue
    title, updated = '', ''
    for i, row in enumerate(rows):
        if row.get('type') != 'message':
            continue
        msg = row.get('message', {})
        role = msg.get('role')
        stamp = timestamp(row.get('timestamp') or msg.get('timestamp'))
        updated = max(updated, stamp)
        event_id = sid + ':' + str(row.get('id', i))
        text = plain(msg.get('content'))
        if role in ('user', 'assistant') and text.strip():
            safe_text = redact(text)
            if role == 'user' and not title:
                title = safe_text.replace('\n', ' ')[:72]
            db.execute('INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?)', (event_id, sid, role, stamp, safe_text))
        if role == 'assistant' and isinstance(msg.get('content'), list):
            for call in msg['content']:
                if call.get('type') not in ('toolCall', 'tool_use'):
                    continue
                name = call.get('name', 'tool')
                args = call.get('arguments', {})
                if not isinstance(args, dict):
                    args = {}
                command = str(args.get('command', ''))
                script = SCANNER_INVOCATION.findall(command)
                # Store script names only, never full commands or tool output (may contain secrets).
                label = ', '.join(dict.fromkeys(script))[:150] or name
                task_id = sid + ':' + str(call.get('id', event_id))
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,tool=excluded.tool,started=excluded.started', (task_id, sid, name, label, stamp, None, 'unconfirmed'))
        if role == 'toolResult':
            details = msg.get('details') or {}
            state = details.get('status')
            exit_code = details.get('exitCode')
            status = 'failed' if msg.get('isError') or state in ('error', 'failed') or (exit_code is not None and exit_code != 0) else ('running' if state == 'running' else 'completed')
            db.execute('UPDATE tasks SET finished=?,status=? WHERE id=?', (stamp, status, sid + ':' + str(msg.get('toolCallId'))))
            task = db.execute('SELECT label FROM tasks WHERE id=?', (sid + ':' + str(msg.get('toolCallId')),)).fetchone()
            scanners = {'a-scan.py', 'hk-scan.py', 'yaogu-scan.py', 'screen_ashare.py', 'trinity.py'}
            if task and scanners.intersection(task['label'].split(', ')) and text.strip():
                db.execute('INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?)', (event_id, sid, stamp, task['label'], redact(text)))
        # Only link snapshots explicitly mentioned in source events; never infer by time.
        if role in ('assistant', 'user', 'toolResult'):
            for filename in set(re.findall(r'scan-(?:a|hk)-\d{8}-\d{4}\.json', text)):
                db.execute('UPDATE scans SET session_id=?,message_id=? WHERE id=?', (sid, event_id if role in ('user', 'assistant') else None, filename))
    for result in completed_selections(rows, sid, SCANNER_INVOCATION, keep_all=True):
        safe_text, safe_prompt = redact(result['text']), redact(result['prompt'])
        data = structure_model_result(safe_text, safe_prompt)
        data['report_ids'] = result['report_ids']
        db.execute('INSERT OR REPLACE INTO model_events VALUES(?,?,?,?,?,?,?,?,?)', (result['id'], sid, result['user_message_id'], safe_prompt, timestamp(result['timestamp']), result['provider'], result['model'], safe_text, json.dumps(data, ensure_ascii=False)))
        db.execute('INSERT INTO model_results VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_message_id) DO UPDATE SET id=excluded.id,prompt=excluded.prompt,timestamp=excluded.timestamp,provider=excluded.provider,model=excluded.model,text=excluded.text,data=excluded.data WHERE excluded.timestamp>=model_results.timestamp', (result['id'], sid, result['user_message_id'], safe_prompt, timestamp(result['timestamp']), result['provider'], result['model'], safe_text, json.dumps(data, ensure_ascii=False)))
    db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?,?)', (sid, channels.get(sid, 'unknown'), title or '历史会话', updated))
    save_source(db, path, stat)


def scan_summary(data):
    """Keep quote universe distinct from computed recommendations."""
    universe = data.get('universe', {})
    stocks = list(universe.values()) if isinstance(universe, dict) else universe
    return {
        'stamp': data.get('stamp'),
        'stocks': stocks,
        'kline_count': len(data.get('kline', {})),
        'emotion': data.get('emotion', {}),
        'freshness': data.get('freshness', {}),
        'industry': data.get('industry', []),
        'concept': data.get('concept', []),
        'limit_up': len(data.get('zt', [])),
        'limit_down': len(data.get('dt', [])),
        'missing': [key for key in ('industry', 'concept', 'moneyflow') if key in data and not data[key]],
    }


def sync(db_path, workspace, sessions):
    workspace, sessions = Path(workspace), Path(sessions)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = connect(db_path)
    structure_version = db.execute("SELECT value FROM meta WHERE key='model_structure_version'").fetchone()
    if not structure_version or structure_version[0] != str(STRUCTURE_VERSION):
        with db:
            for record in db.execute('SELECT id,prompt,text,data FROM model_results').fetchall():
                previous = json.loads(record['data'])
                data = structure_model_result(record['text'], record['prompt'])
                data['report_ids'] = previous.get('report_ids', [])
                db.execute('UPDATE model_results SET data=? WHERE id=?', (json.dumps(data, ensure_ascii=False), record['id']))
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('model_structure_version', str(STRUCTURE_VERSION)))
    version = db.execute("SELECT value FROM meta WHERE key='import_version'").fetchone()
    if not version or version[0] != IMPORT_VERSION:
        # Rebuild derived reports when parser semantics change; originals stay untouched.
        db.execute('DELETE FROM reports')
        db.execute('DELETE FROM sources')
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('import_version', IMPORT_VERSION))
        db.commit()
    errors = []
    imported = 0

    def process(path, action):
        nonlocal imported
        try:
            if changed(db, path):
                with db:
                    action()
                imported += 1
        except Exception as exc:
            # Report source filename and exception type without leaking file content.
            errors.append(path.name + ': ' + type(exc).__name__)
            LOG.warning('Import failed: %s (%s)', path.name, type(exc).__name__)

    scan_dir = workspace / 'skills/a-share-short-term-analysis/scans'
    for path in sorted(scan_dir.glob('scan-*.json')):
        def load_scan(path=path):
            stat = path.stat()
            data = scan_summary(json.loads(path.read_text(encoding='utf-8')))
            match = re.fullmatch(r'scan-(a|hk)-(\d{8}-\d{4})\.json', path.name)
            if not match:
                return
            stamp = dt.datetime.strptime(match[2], '%Y%m%d-%H%M').replace(tzinfo=dt.timezone(dt.timedelta(hours=8))).astimezone(UTC).isoformat()
            db.execute('INSERT INTO scans VALUES(?,?,?,?,NULL,NULL) ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp,market=excluded.market,data=excluded.data', (path.name, stamp, match[1], json.dumps(data, ensure_ascii=False)))
            save_source(db, path, stat)
        process(path, load_scan)

    try:
        channels = session_channels(sessions)
    except Exception as exc:
        channels = {}
        errors.append('sessions.json: ' + type(exc).__name__)
    # Trajectory files duplicate content and contain expanded prompts; exclude them.
    for path in sorted(sessions.glob('*.jsonl*')):
        if '.trajectory' in path.name or not re.match(r'^[\w-]+\.jsonl(?:$|\.reset\.)', path.name):
            continue
        process(path, lambda path=path: import_session(db, path, channels))
    for sid, channel in channels.items():
        db.execute('UPDATE sessions SET channel=? WHERE id=?', (channel, sid))

    notes = list((workspace / 'memory').glob('*.md'))
    if (workspace / 'MEMORY.md').is_file():
        notes.append(workspace / 'MEMORY.md')
    for path in notes:
        def load_note(path=path):
            stat = path.stat()
            db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?)', (path.name, path.stem, dt.datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(), redact(path.read_text(encoding='utf-8'))))
            save_source(db, path, stat)
        process(path, load_note)

    # Archive only explicitly allowed rule files, never general configuration.
    rules = []
    rule_paths = [workspace / name for name in ('AGENTS.md', 'SOUL.md', 'IDENTITY.md', 'HEARTBEAT.md')]
    rule_paths += sorted((workspace / 'skills').glob('*/SKILL.md'))
    for path in rule_paths:
        if not path.is_file():
            continue
        try:
            rules.append({'id': str(path.relative_to(workspace)).replace('\\', '/'),
                          'title': path.parent.name if path.name == 'SKILL.md' else path.stem,
                          'timestamp': dt.datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                          'text': redact(path.read_text(encoding='utf-8'))})
        except (OSError, UnicodeError) as exc:
            errors.append(path.name + ': ' + type(exc).__name__)
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('rules', json.dumps(rules, ensure_ascii=False)))
    scheduler = {'status': 'unknown', 'items': [], 'note': '尚未配置可核验的调度数据源'}
    if os.getenv('STOCK_CRON'):
        try:
            jobs = json.loads(Path(os.environ['STOCK_CRON']).read_text(encoding='utf-8'))['jobs']
            if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
                raise ValueError('Expected jobs list')
            scheduler = {'status': 'available', 'items': [], 'note': '仅展示当前智能体的调度配置；执行状态以调度器记录为准'}
            for job in jobs:
                if job.get('agentId') == sessions.parent.name:
                    scheduler['items'].append({key: job.get(key) for key in ('id', 'name', 'enabled', 'schedule', 'state')})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            scheduler['note'] = '调度数据不可读取：' + type(exc).__name__
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('scheduler', redact(json.dumps(scheduler, ensure_ascii=False))))

    position_path = workspace / 'positions.json'
    try:
        positions = json.loads(position_path.read_text(encoding='utf-8'))
        if not isinstance(positions, list):
            raise ValueError('Expected a position list')
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('positions', json.dumps(positions, ensure_ascii=False)))
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('positions_updated', dt.datetime.fromtimestamp(position_path.stat().st_mtime, UTC).isoformat()))
    except Exception as exc:
        errors.append('positions.json: ' + type(exc).__name__)
    if not sessions.is_dir():
        errors.append('会话目录不可访问')
    if not workspace.is_dir():
        errors.append('工作区不可访问')
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
    while True:
        try:
            info = sync(os.getenv('STOCK_DB', '/var/lib/stock2027/console.db'), os.environ['STOCK_WORKSPACE'], os.environ['STOCK_SESSIONS'])
            LOG.info('Sync complete: %d updated files; %d errors', info['imported'], len(info['errors']))
        except Exception:
            LOG.exception('Sync failed')
            if args.once:
                raise
        if args.once:
            return
        time.sleep(max(5, int(os.getenv('SYNC_INTERVAL', '30'))))


if __name__ == '__main__':
    main()
