"""Present explicitly reported scanner groups without generating recommendations."""
import re

LABELS = {'pass': '条件通过', 'watch': '待观察', 'reserve': '备选', 'reject': '未通过'}
SOURCES = {'yaogu-scan.py': '妖股筛选', 'a-scan.py': 'A股综合选股', 'hk-scan.py': '港股综合选股', 'screen_ashare.py': 'A股多因子选股', 'trinity.py': '三合一选股'}
NUMBER = r'[+-]?\d+(?:\.\d+)?'


def friendly_reason(text):
    parts = []
    for token in text.split('·'):
        volume = re.search(r'量能(' + NUMBER + r')×', token)
        position = re.search(r'120分位(' + NUMBER + r')%', token)
        if volume:
            parts.append('成交量为平时的' + volume[1] + '倍')
        elif position:
            parts.append('股价位于近120日区间' + ('下半部' if float(position[1]) <= 50 else '上半部'))
        elif '死水' in token or '低换手' in token:
            parts.append('前期交易清淡')
        elif 'RR' in token and '不足' in token:
            parts.append('收益风险比不足')
        elif '站上' in token and '均线' in token:
            parts.append(token.replace('均线', '条均线'))
        elif '上影' in token:
            parts.append(token.replace('上影', '上影线'))
        elif '量能不足' in token:
            parts.append('成交量不足')
    return '；'.join(dict.fromkeys(parts)) or text


def parse_selection(text, script):
    if script not in SOURCES or any(x in text[:1000] for x in ('冒烟测试', '历史回归验证', '#!/usr/bin', 'Traceback (most recent call last)')):
        return None
    groups = {key: {'label': label, 'total': None} for key, label in LABELS.items()}
    candidates, group, seen_group, previous, table_schema = [], None, False, None, False
    for line in text.splitlines():
        if '┌' in line:
            group = None
            for pattern, value in ((r'否决|封板', 'reject'), (r'A级|进攻', 'pass'), (r'C级|埋伏', 'reserve'), (r'B级|观察', 'watch')):
                if re.search(pattern, line):
                    group = value
                    seen_group = True
                    count = re.search(r'[（(]\s*(\d+)\s*只\s*[）)]', line)
                    if count:
                        groups[group]['total'] = int(count[1])
                    break
            previous = None
            continue
        if '代码' in line and '套牢%' in line and '2ATR止损' in line:
            table_schema = True
            continue
        if previous is not None and '└' in line and '止损' in line:
            for field, pattern in [('stop', r'止损(' + NUMBER + ')'), ('target', r'目标(' + NUMBER + ')'), ('rr', r'RR(' + NUMBER + ')'), ('shares', r'(\d+)股')]:
                match = re.search(pattern, line)
                if match:
                    previous[field] = float(match[1])
            reason = line.split('|')[-1].strip()
            previous['raw_reason'] = reason
            previous['reason'] = friendly_reason(reason)
            continue
        if group not in ('pass', 'watch', 'reserve'):
            continue
        clean = line.lstrip(' │')
        row = re.match(r'\d+\.\s+(\d{6})\s+(\S+)\s+(' + NUMBER + r')\s+(' + NUMBER + r')%.*?得分\s*(' + NUMBER + ')', clean)
        candidate = None
        if row:
            candidate = {'code': row[1], 'name': row[2], 'price': float(row[3]), 'pct': float(row[4]), 'score': float(row[5])}
        elif table_schema:
            fields = clean.split()
            if len(fields) >= 14 and re.fullmatch(r'\d{5,6}', fields[0]) and all(re.fullmatch(NUMBER, item) for item in fields[3:13]):
                reason = ' '.join(fields[13:]).replace('⚠', '').strip()
                candidate = {'code': fields[0], 'name': fields[1], 'sector': fields[2], 'pct': float(fields[3]), 'volume_ratio': float(fields[9]), 'atr': float(fields[10]), 'stop': float(fields[11]), 'reason': reason, 'raw_reason': reason}
        if candidate:
            candidate['group'] = group
            candidates.append(candidate)
            previous = candidate
    if not seen_group:
        return None
    # Repeated headings/partial tool captures can contain the same stock twice.
    candidates = list({(item['group'], item['code']): item for item in candidates}.values())
    for key, value in groups.items():
        value['shown'] = sum(item['group'] == key for item in candidates)
    return {'source': SOURCES[script], 'market': 'hk' if script == 'hk-scan.py' else 'a', 'groups': groups, 'candidates': candidates, 'delayed': '行情延迟' in text or '个股行情延迟' in text}
