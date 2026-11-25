"""Index completed model replies; retain the original text as the authority."""
import re


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(x.get('text', '') for x in content if isinstance(x, dict) and x.get('type') == 'text')
    return ''


def technical_request(prompt):
    return bool(re.search(r'建立|创建|开发|编写|修复|安装|配置|脚本|部署|代码|界面|API|密钥|怎么(?:做|弄)|步骤|流程|原理|大模型能力', prompt, re.I))


def selection_request(prompt):
    if technical_request(prompt):
        return False
    return bool(re.search(r'全局选股|(?:帮我|给我|重新|再|今天|今日|现在|全市场|A股|港股).{0,10}(?:选股|筛股|选几只|选一只)|^(?:选股|选几只股票|扫描全市场)[。！!\s]*$', prompt, re.I))


def completed_selections(rows, session_id, scanner_pattern, keep_all=False):
    """Use parent ancestry to associate a final reply with its actual request."""
    by_id = {str(row.get('id', i)): row for i, row in enumerate(rows)}
    results = {}
    sequential = []
    for i, row in enumerate(rows):
        message = row.get('message', {})
        if row.get('type') != 'message':
            continue
        sequential.append(row)
        if message.get('role') != 'assistant' or message.get('stopReason') != 'stop':
            continue
        content = message.get('content', [])
        if isinstance(content, list) and any(x.get('type') in ('toolCall', 'tool_use') for x in content if isinstance(x, dict)):
            continue
        text = content_text(content).strip()
        if not text or text in ('NO_REPLY', 'HEARTBEAT_OK'):
            continue
        chain, node, seen = [], row, set()
        while node:
            marker = str(node.get('id', ''))
            if marker in seen:
                break
            seen.add(marker)
            chain.append(node)
            if node.get('message', {}).get('role') == 'user':
                break
            parent = node.get('parentId')
            node = by_id.get(str(parent)) if parent is not None else None
        if chain[-1].get('message', {}).get('role') != 'user':
            # Legacy unthreaded logs have an explicit sequential turn boundary.
            if 'parentId' in row:
                continue
            user_index = next((j for j in range(len(sequential) - 1, -1, -1) if sequential[j].get('message', {}).get('role') == 'user'), None)
            if user_index is None:
                continue
            chain = list(reversed(sequential[user_index:]))
        request = chain[-1]
        prompt = content_text(request.get('message', {}).get('content')).strip()
        # A completed reply about an API failure is not a stock-selection conclusion.
        if not re.search(r'\d{5,6}|空仓|不开新仓|不推荐|不选|不买|没有.{0,8}(?:标的|股票|候选)|暂无.{0,8}(?:标的|股票|候选)', text):
            continue
        calls, reports = set(), []
        for event in reversed(chain):
            msg = event.get('message', {})
            parts = msg.get('content', [])
            if msg.get('role') == 'assistant' and isinstance(parts, list):
                for part in parts:
                    arguments = part.get('arguments', {}) if isinstance(part, dict) else {}
                    if isinstance(arguments, dict) and scanner_pattern.search(str(arguments.get('command', ''))):
                        calls.add(part.get('id'))
            if msg.get('role') == 'toolResult' and msg.get('toolCallId') in calls:
                reports.append(session_id + ':' + str(event.get('id')))
        if technical_request(prompt) or not (selection_request(prompt) or reports):
            continue
        result = {'id': session_id + ':' + str(row.get('id', i)), 'session_id': session_id, 'user_message_id': session_id + ':' + str(request.get('id')), 'prompt': prompt, 'timestamp': row.get('timestamp') or message.get('timestamp'), 'provider': message.get('provider', ''), 'model': message.get('model', ''), 'text': text, 'report_ids': reports}
        # Only the last completed reply in this request/branch is indexed.
        results[result['id'] if keep_all else result['user_message_id']] = result
    return list(results.values())


def clean_line(line):
    return re.sub(r'^\s*(?:#{1,6}\s*|[-*]\s+)', '', line).replace('**', '').replace('`', '').strip()


def extract_model_stocks(text):
    """Copy explicit names, decisions and plan strings; never fill from a scanner."""
    stocks, context, context_quote, active, table_headers = [], '', '', None, None
    name_code = re.compile(r'([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z·*\-]{1,17})\s*[（(]?((?:sh|sz|hk)?\d{5,6})(?!\d)[）)]?')
    action = re.compile(r'次选不给买点|看着但今天不买|逐个否决|明确不做|不买清单|暂不买|不推荐|不买|否决|首选|推荐|备选|次选|观察|候选|买入')
    fenced = False
    for raw in text.splitlines():
        if raw.strip().startswith('```'):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = clean_line(raw)
        if raw.strip().startswith('|'):
            cells = [clean_line(cell) for cell in raw.strip().strip('|').split('|')]
            if any(cell in ('股票', '标的', '名称', '股票名称') for cell in cells) and any(cell in ('结论', '操作', '建议', '最终结论', '模型结论', '判断') for cell in cells):
                table_headers = cells
            elif table_headers and len(cells) == len(table_headers):
                values = dict(zip(table_headers, cells))
                name = next((values[key] for key in ('股票', '标的', '名称', '股票名称') if key in values), '')
                code = next((values[key] for key in ('代码', '股票代码') if key in values), '')
                decision = next((values[key] for key in ('结论', '操作', '建议', '最终结论', '模型结论', '判断') if key in values), '')
                match = name_code.search(name + ' ' + code)
                if match and decision:
                    entry = {'name': match[1], 'code': match[2], 'decision': decision, 'decision_evidence': raw, 'source_line': line}
                    for field, labels in {'entry': ('买入', '买点', '买入区间', '买入价', '入场价'), 'stop': ('止损', '止损价'), 'target': ('目标', '目标价'), 'position': ('仓位',), 'shares': ('股数',), 'reason': ('理由', '原因', '推荐理由')}.items():
                        value = next((values[label] for label in labels if label in values), '')
                        if value:
                            entry[field] = value
                    stocks.append(entry)
            continue
        table_headers = None
        if active is not None:
            plan = re.match(r'^(买入(?:区间|价)?|买点|止损(?:价)?|(?:第一|第二)?目标(?:价)?|仓位|股数|失效条件|放弃条件)(?:\s*[:：]\s*|\s*(?=\d))(.+)', line)
            if plan:
                key = 'entry' if plan[1].startswith(('买入', '买点')) else 'stop' if plan[1].startswith('止损') else 'target' if '目标' in plan[1] else 'position' if plan[1] in ('仓位', '股数') else 'invalid'
                if key not in active:
                    active[key] = plan[2].strip()
                continue
        heading = bool(re.match(r'^\s*#{1,6}\s|^\s*\*\*|^\s*[-*]\s+\*\*', raw))
        label = action.search(line[:70]) if heading else None
        if heading and label:
            context, context_quote, active = label[0], raw, None
        # A new section with no stock or action closes the preceding plan.
        if re.match(r'^\s*#{1,6}\s', raw) and not label:
            active = None
        matches = list(name_code.finditer(line)) if heading else []
        if matches and context:
            entries = []
            for match in matches:
                entry = {'name': match[1], 'code': match[2], 'decision': context, 'decision_evidence': context_quote, 'source_line': line}
                stocks.append(entry)
                entries.append(entry)
            active = entries[0] if len(entries) == 1 else None
            continue
    return list({(item['code'], item['decision']): item for item in stocks}.values())


STRUCTURE_VERSION = 2
STOCK_FIELDS = ('name', 'code', 'decision', 'reason', 'entry', 'stop', 'target', 'shares', 'amount', 'position', 'invalid', 'risk')
PLAN_LABELS = {
    'entry': r'买入条件|买入计划|买入区间|买入价|买入|买点|入场价|入场条件',
    'stop': r'止损价|止损', 'target': r'第一目标|目标价|目标',
    'position': r'仓位|股数', 'invalid': r'失效条件|放弃条件',
    'risk': r'最大亏损|风险敞口|账户风险|单笔风险',
}


def stock_block(lines, stock, stock_headings):
    start = next((i for i, line in enumerate(lines) if clean_line(line) == stock['source_line']), None)
    if start is None:
        return []
    block = [lines[start]]
    if lines[start].strip().startswith('|'):
        return block
    for line in lines[start + 1:]:
        plain = clean_line(line)
        heading = bool(re.match(r'^\s*#{1,6}\s|^\s*\*\*|^\s*[-*]\s+\*\*', line))
        if re.fullmatch(r'\s*-{3,}\s*', line):
            break
        if heading and plain in stock_headings and plain != stock['source_line']:
            break
        if heading and re.search(r'^(?:不买清单|明确不做|逐个否决|看着但今天不买|首选|推荐|次选|备选)[：:（(\s]', plain):
            break
        if re.match(r'^\s*#{1,6}\s', line) and not re.search(r'计划|理由|为什么|失效|放弃|止损|目标|仓位|买入|条件', plain):
            break
        block.append(line)
    return block


def structured_stock(stock, lines, stock_headings):
    result = {field: stock.get(field) or None for field in STOCK_FIELDS}
    for field in ('entry', 'stop', 'target', 'shares', 'amount', 'position', 'invalid', 'risk'):
        if result[field] in ('—', '-', '--', '未提供', '暂无', '待定', '未明确'):
            result[field] = None
    block = stock_block(lines, stock, stock_headings)
    evidence = {field: next((line for line in block if str(stock[field]) in clean_line(line)), stock['source_line']) for field in STOCK_FIELDS if result.get(field)}
    evidence['decision'] = stock.get('decision_evidence', stock['source_line'])
    normalized = [clean_line(line) for line in block]
    for index, line in enumerate(normalized[1:], 1):
        for field, labels in PLAN_LABELS.items():
            match = re.match(r'^(?:' + labels + r')(?:\s*[:：]\s*|\s*(?=\d))(.*)', line)
            if not match:
                continue
            value = match[1].strip()
            quote = [block[index]]
            if not value:
                follow = []
                for j in range(index + 1, len(block)):
                    if not normalized[j]:
                        if follow:
                            break
                        continue
                    if re.match(r'^\s*#{1,6}\s|^\s*\*\*', block[j]):
                        break
                    follow.append(normalized[j])
                    quote.append(block[j])
                value = '\n'.join(follow)
            if value and field not in evidence:
                result[field] = value
                evidence[field] = '\n'.join(quote)
    # Conditional plans stay conditional instead of becoming unconditional buys.
    for index, line in enumerate(normalized[1:], 1):
        if not result['entry'] and re.search(r'如果.{0,8}(?:买|参与)|若.{0,8}(?:买入|建仓)', line) and re.search(r'\d+股|买入[：:]|要买[：:]', line):
            result['entry'] = line
            evidence['entry'] = block[index]
        for field, label in (('stop', '止损'), ('target', '目标价|目标')):
            if result[field]:
                continue
            match = re.search(r'(?<![\u4e00-\u9fff])(?:' + label + r')[：:\s]*(\d+(?:\.\d+)?[^，。；;\n]*)', line)
            if match:
                result[field] = match[1].strip()
                evidence[field] = block[index]
    # Size and allocation are copied only from explicit entry/position plan text.
    sizing = '\n'.join(result[key] or '' for key in ('entry', 'position'))
    for field, pattern in (
        ('shares', r'(?<!\d)\d[\d,]*\s*股(?![份价])'),
        ('position', r'(?:占账户|仓位)[：:\s]*\d+(?:\.\d+)?%|\d+(?:\.\d+)?(?:%仓位|%仓|成仓(?:位)?)'),
    ):
        if not result[field]:
            matches = list(dict.fromkeys(re.findall(pattern, sizing)))
            if len(matches) == 1:
                result[field] = matches[0]
                evidence[field] = evidence.get('entry') or evidence.get('position') or stock['source_line']
    if not result['amount']:
        currency = r'\d[\d,]*(?:\.\d+)?\s*(?:万元|万港元|港元|元)'
        amounts = []
        for pattern in (r'(?:金额|合计|总额|投入资金)[：:\s]*([≈约]?\s*' + currency + ')', r'\d[\d,]*\s*股\s*(?:[≈约/=]|，约|，金额|，合计)\s*(' + currency + ')'):
            amounts.extend(re.findall(pattern, sizing))
        amounts = list(dict.fromkeys(amounts))
        if len(amounts) == 1:
            result['amount'], evidence['amount'] = amounts[0], evidence.get('entry') or evidence.get('position') or stock['source_line']
    # Prefer an explicit reason paragraph; otherwise retain the heading's explanation.
    reason = stock.get('reason')
    for index, line in enumerate(normalized):
        match = re.match(r'^(?:理由|原因|选它的逻辑|推荐理由|为什么是它|为什么选它)[：:]\s*(.*)', line)
        if match:
            values = [match[1]] if match[1] else []
            quote = [block[index]]
            if not values:
                for j in range(index + 1, len(block)):
                    if not normalized[j] and values:
                        break
                    if any(re.match(r'^(?:' + labels + r')[：:]', normalized[j]) for labels in PLAN_LABELS.values()):
                        break
                    if normalized[j]:
                        values.append(normalized[j])
                        quote.append(block[j])
            if values:
                reason, evidence['reason'] = '\n'.join(values), '\n'.join(quote)
                break
    if not reason and normalized:
        heading_reason = re.split(r'\s+[—–]\s+', normalized[0], maxsplit=1)
        if len(heading_reason) == 2 and len(heading_reason[1]) > 10:
            reason, evidence['reason'] = heading_reason[1], block[0]
        else:
            paragraph = []
            for index in range(1, len(block)):
                line = normalized[index]
                if not line:
                    if paragraph:
                        break
                    continue
                if re.match(r'^\s*#{1,6}\s|^\s*\*\*', block[index]) or any(re.match(r'^(?:' + label + r')(?:[：:]|\s*\d)', line) for label in PLAN_LABELS.values()):
                    break
                if not line.startswith('|'):
                    paragraph.append(block[index])
            if paragraph:
                reason = '\n'.join(clean_line(line) for line in paragraph)
                evidence['reason'] = '\n'.join(paragraph)
    result['reason'] = reason
    result['source_line'] = stock['source_line']
    result['source_text'] = '\n'.join(block) if block else stock['source_line']
    result['evidence'] = evidence
    return result


def structure_model_result(text, prompt=''):
    lines = text.splitlines()
    cleaned = [clean_line(line) for line in lines if line.strip()]
    headline = cleaned[0] if cleaned else None
    summary = {'conclusion': headline, 'market_state': None, 'participation': None, 'overall_position': None}
    evidence = {'conclusion': next((line for line in lines if line.strip()), None)}
    for raw in lines:
        line = clean_line(raw)
        for field, label in (('market_state', r'大盘|市场判断|市场环境|行情判断'), ('overall_position', r'总仓位|总体仓位')):
            match = re.match(r'^(?:' + label + r')[：:]\s*(.+)', line)
            if match and summary[field] is None:
                summary[field], evidence[field] = match[1], raw
    if headline and re.search(r'空仓|今天不做|什么都不做|不开新仓|不参与|暂不买', headline):
        summary['participation'], evidence['participation'] = headline, evidence['conclusion']
    markets = [market for market in ('A股', '港股') if market in (prompt + '\n' + (headline or ''))]
    summary['markets'], summary['evidence'] = markets, evidence
    raw_stocks = extract_model_stocks(text)
    headings = {stock['source_line'] for stock in raw_stocks}
    stocks = [structured_stock(stock, lines, headings) for stock in raw_stocks]
    return {'schema_version': STRUCTURE_VERSION, 'extraction': 'source_text', 'summary': summary, 'stocks': stocks}
