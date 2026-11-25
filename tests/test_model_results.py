import unittest

from app.collector import SCANNER_INVOCATION
from app.model_results import completed_selections, extract_model_stocks, structure_model_result


def event(identifier, role, content, parent=None, stop=None, **extra):
    return {'type': 'message', 'id': identifier, 'parentId': parent, 'timestamp': '2026-11-25T01:00:00Z', 'message': {'role': role, 'content': content, 'stopReason': stop, **extra}}


class ModelResultsTest(unittest.TestCase):
    def test_event_history_preserves_every_completed_reply_for_same_request(self):
        rows = [event('u', 'user', '全局选股'), event('f1', 'assistant', '**推荐：测试甲600001**', 'u', 'stop'), event('f2', 'assistant', '今天不买600001，空仓。', 'f1', 'stop')]
        self.assertEqual(['s:f1', 's:f2'], [r['id'] for r in completed_selections(rows, 's', SCANNER_INVOCATION, keep_all=True)])
        self.assertEqual(['s:f2'], [r['id'] for r in completed_selections(rows, 's', SCANNER_INVOCATION)])

    def test_only_completed_model_reply_is_indexed(self):
        rows = [
            event('u', 'user', '全局选股'),
            event('call', 'assistant', [{'type': 'text', 'text': '正在扫描'}, {'type': 'toolCall', 'id': 'scan', 'name': 'exec', 'arguments': {'command': 'python3 templates/a-scan.py'}}], 'u', 'toolUse'),
            event('tool', 'toolResult', '程序候选测试甲600001', 'call', toolCallId='scan'),
            event('final', 'assistant', '**推荐：测试乙 600002**\n- 买入：20–21，100股\n- 止损：18.50', 'tool', 'stop', provider='deepseek', model='test-model'),
        ]
        results = completed_selections(rows, 'session', SCANNER_INVOCATION)
        self.assertEqual(1, len(results))
        self.assertEqual('session:final', results[0]['id'])
        self.assertEqual(['session:tool'], results[0]['report_ids'])
        self.assertNotIn('测试甲', results[0]['text'])
        self.assertEqual('test-model', results[0]['model'])

    def test_parent_chain_does_not_use_an_unrelated_latest_request(self):
        rows = [event('u1', 'user', '全局选股'), event('u2', 'user', '建立一个筛选器', 'u1'), event('f', 'assistant', '**推荐：测试乙600002**', 'u1', 'stop')]
        result = completed_selections(rows, 's', SCANNER_INVOCATION)
        self.assertEqual('s:u1', result[0]['user_message_id'])

    def test_technical_incomplete_and_unthreaded_orphan_replies_are_excluded(self):
        rows = [
            event('dev', 'user', '建立一个全局选股筛选器'), event('dev-f', 'assistant', '**推荐：测试甲600001**', 'dev', 'stop'),
            event('u', 'user', '全局选股'), event('partial', 'assistant', '**推荐：测试甲600001**', 'u', 'length'),
            event('orphan', 'assistant', '**推荐：测试乙600002**', 'missing', 'stop'),
            event('silent', 'assistant', 'NO_REPLY', 'u', 'stop'),
        ]
        self.assertEqual([], completed_selections(rows, 's', SCANNER_INVOCATION))

    def test_abstention_is_still_a_final_conclusion(self):
        rows = [event('u', 'user', '全局选股'), event('f', 'assistant', '今天没有合适标的，保持空仓。', 'u', 'stop')]
        result = completed_selections(rows, 's', SCANNER_INVOCATION)
        self.assertEqual(1, len(result))
        self.assertEqual([], extract_model_stocks(result[0]['text']))

    def test_explicit_plans_keep_original_strings_and_decisions(self):
        text = '''**推荐：测试乙 600002**
- **买入**：20.00–21.00，100股（10%仓位）
- **止损 18.50**（仅示例）
- **目标**：23 / 24

**次选不给买点：测试丙 600003**
没有明确给出计划。
'''
        stocks = extract_model_stocks(text)
        self.assertEqual(['600002', '600003'], [s['code'] for s in stocks])
        self.assertEqual('20.00–21.00，100股（10%仓位）', stocks[0]['entry'])
        self.assertEqual('18.50（仅示例）', stocks[0]['stop'])
        self.assertEqual('23 / 24', stocks[0]['target'])
        self.assertEqual('次选不给买点', stocks[1]['decision'])
        self.assertNotIn('entry', stocks[1])
        self.assertNotIn('stop', stocks[1])

    def test_table_decisions_are_not_inherited_from_section_title(self):
        text = '''## 推荐对比
| 股票 | 代码 | 结论 | 买入区间 | 止损 |
|---|---|---|---|---|
| 测试甲 | 600001 | 不买 | — | — |
| 测试乙 | 600002 | 等回踩 | 20–21 | 18 |
'''
        stocks = extract_model_stocks(text)
        self.assertEqual(['不买', '等回踩'], [s['decision'] for s in stocks])
        self.assertEqual('20–21', stocks[1]['entry'])

    def test_code_fences_are_not_investment_decisions(self):
        text = '```\n**推荐：测试甲600001**\n```'
        self.assertEqual([], extract_model_stocks(text))

    def test_structured_summary_and_position_are_literal_excerpts(self):
        text = '''**全局选股：谨慎参与**
**大盘：指数仍在均线下方**
总仓位：不超过20%

**推荐：测试甲600001**
理由：板块走强，个股放量。
- 买入：20–21，即100股≈2,000元（10%仓位）
- 止损：18.50
- 目标：23 / 24
**失效条件：**
- 跌破19则放弃
- 三天未启动则取消
'''
        result=structure_model_result(text,'A股全局选股')
        self.assertEqual(2,result['schema_version'])
        self.assertEqual('指数仍在均线下方',result['summary']['market_state'])
        self.assertEqual('不超过20%',result['summary']['overall_position'])
        self.assertEqual(['A股'],result['summary']['markets'])
        stock=result['stocks'][0]
        self.assertEqual('100股',stock['shares'])
        self.assertEqual('2,000元',stock['amount'])
        self.assertEqual('10%仓位',stock['position'])
        self.assertEqual('板块走强，个股放量。',stock['reason'])
        self.assertIn('三天未启动则取消',stock['invalid'])
        self.assertIn('买入',stock['evidence']['amount'])

    def test_conditional_plan_stays_conditional_and_cannot_leak_to_next_stock(self):
        text='''**次选不给买点：测试甲600001**
如果真的要买：100股≈2,000元，止损18.50（仅条件计划）。
---
**观察：测试乙600002**
还没有给出买入计划。
'''
        stocks=structure_model_result(text)['stocks']
        self.assertEqual('次选不给买点',stocks[0]['decision'])
        self.assertTrue(stocks[0]['entry'].startswith('如果真的要买'))
        self.assertEqual('18.50（仅条件计划）',stocks[0]['stop'])
        for key in ('entry','stop','target','shares','amount','position'):
            self.assertIsNone(stocks[1][key])

    def test_unit_price_is_not_treated_as_total_investment(self):
        text='''**推荐：测试甲600001**
- 买入：约20元买入100股
'''
        stock=structure_model_result(text)['stocks'][0]
        self.assertEqual('100股',stock['shares'])
        self.assertIsNone(stock['amount'])

    def test_conflicting_sizes_are_left_unknown(self):
        text='''**备选：测试甲600001**
- 买入：若回踩则100股，若突破则200股
'''
        stock=structure_model_result(text)['stocks'][0]
        self.assertIsNone(stock['shares'])
        self.assertIn('100股',stock['entry'])
        self.assertIn('200股',stock['entry'])

    def test_inherited_decision_keeps_the_actual_heading_as_evidence(self):
        text='''**逐个否决，理由写清楚**
**测试港股00981** — 结构没有突破，不能参与。
'''
        stock=structure_model_result(text)['stocks'][0]
        self.assertEqual('逐个否决',stock['decision'])
        self.assertIn('逐个否决',stock['evidence']['decision'])
        self.assertEqual('结构没有突破，不能参与。',stock['reason'])


if __name__ == '__main__':
    unittest.main()
