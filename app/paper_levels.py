"""Read fixed exit prices from the original recommendation for a paper holding."""
import math
import re

from app.model_results import clean_line


LABELS = {'stop': r'止损(?:价|位|点)?', 'target': r'(?:第一|首个)?(?:止盈|目标)(?:价|位|点)?'}


def labeled_value(text, label):
    matches = []
    for line in text.splitlines():
        match = re.fullmatch(r'(?:' + label + r')\s*(?:[：:]\s*|(?=\d))(.+)', clean_line(line))
        if match:
            matches.append(match[1].strip())
    return matches[0] if len(set(matches)) == 1 else None


def fixed_price(value, label):
    text = str(value or '').strip()
    # A conditional close, a range, or a partial exit is not a fixed full-position instruction.
    if re.search(r'收盘|尾盘|确认后|连续|若|如果|分批|分段|一半|减半|半仓|卖半|部分|减仓|卖出?\s*[一二三四五六七八九\d]|区间|移动止损', text):
        return None
    match = re.match(r'^(?:(?:' + label + r')\s*[：:]?\s*)?(?:跌破|跌至|达到|上触)?\s*'
                     r'(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:港元|元)?(?=$|[（(，,。；;/、\s])', text)
    if not match or re.match(r'\s*(?:[%％股倍万×至到~～–—-]|[:：]\s*\d|,\d|日均|日线|个?交易日)', text[match.end():]):
        return None
    price = float(match[1].replace(',', ''))
    return price if math.isfinite(price) and price > 0 else None


def exit_levels(stock, code, original):
    # Older reports sometimes put the plan in a separate fenced block headed by its ticker.
    blocks = []
    for block in re.findall(r'^[ \t]*\x60{3}[^\n]*\n(.*?)^[ \t]*\x60{3}[ \t]*$', original or '', re.M | re.S):
        tickers = re.findall(r'^标的\s*[：:]\s*(.*)$', block, re.M)
        if len(tickers) == 1 and re.findall(r'(?<!\d)\d{5,6}(?!\d)', tickers[0]) == [code]:
            blocks.append(block)
    levels = {}
    table = {}
    source_line = stock.get('source_line') or ''
    if source_line.startswith('|'):
        headers = []
        for raw in (original or '').splitlines():
            line = clean_line(raw)
            if not line.startswith('|'):
                headers = []
                continue
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            if line == source_line and len(cells) == len(headers):
                table = dict(zip(headers, cells))
                break
            if any(cell in ('股票', '标的', '股票名称', '名称') for cell in cells):
                headers = cells
    for field, label in LABELS.items():
        value = stock.get(field)
        if not value and len(blocks) == 1:
            value = labeled_value(blocks[0], label)
        if not value:
            value = next((v for k, v in table.items() if re.fullmatch(label, k)), None)
        if not value:
            value = labeled_value(stock.get('source_text') or '', label)
        levels[field] = fixed_price(value, label)
    if levels['stop'] and levels['target'] and levels['stop'] >= levels['target']:
        return {'stop': None, 'target': None}
    return levels
