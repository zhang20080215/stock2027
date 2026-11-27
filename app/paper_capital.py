"""Private capital configuration; only the paper worker mutates its ledger."""
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import secrets


def amounts(values):
    if not isinstance(values, dict) or set(values) != {'a', 'hk'}:
        raise ValueError('请分别填写A股和港股账户本金')
    result = {}
    for market, value in values.items():
        try:
            number = Decimal(str(value))
            if not number.is_finite() or not 0 < number <= 1000000000000 or number != number.quantize(Decimal('0.01')):
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise ValueError('本金须大于0，最多两位小数，上限1万亿') from None
        result[market] = float(number)
    return result


def validate_cash(accounts, values):
    for account in accounts:
        cash = round(account['cash'] + values[account['market']] - account['initial'], 2)
        if cash < 0:
            raise ValueError(('A股' if account['market'] == 'a' else '港股') + '本金低于已投入成本，无法调整')


def read_config(path):
    return json.loads(Path(path).read_text()) if path and Path(path).exists() else None


def save_config(path, values):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    config = {'id': secrets.token_hex(16), 'initial': amounts(values)}
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(config, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return config


def apply_config(db, path):
    config = read_config(path)
    if not config:
        return
    previous = db.execute("SELECT value FROM meta WHERE key='paper_capital_config'").fetchone()
    if previous and json.loads(previous[0]).get('id') == config['id']:
        return
    status = {'id': config['id'], 'status': 'applied', 'initial': config['initial']}
    try:
        values = amounts(config['initial'])
        accounts = db.execute('SELECT * FROM paper_accounts').fetchall()
        validate_cash(accounts, values)
        for account in accounts:
            new = values[account['market']]
            db.execute('UPDATE paper_accounts SET initial=?,cash=round(cash+?,2) WHERE market=?',
                       (new, new - account['initial'], account['market']))
    except ValueError as exc:
        status.update(status='error', error=str(exc))
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('paper_capital_config', json.dumps(status, ensure_ascii=False)))
