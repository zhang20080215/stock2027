import unittest

from app.selections import parse_selection


class SelectionTest(unittest.TestCase):
    def test_groups_and_plans_are_read_from_output(self):
        text = '''┌─ A级 · 条件通过 (1 只)
│ 1. 600001 测试甲 10.00 +2.0% 120分位 20% 得分 88.0
│ └ 止损9.50(-5%) 目标11.00(+10%) RR2.0 | 100股≈1000元 | 低换手0.5%·量能3.0×·120分位20%
┌─ B级 · 只观察 (4 只)
│ 1. 600002 测试乙 20.00 +3.0% 120分位 40% 得分 70.0
┌─ C级 · 纯观察池 (2 只)
│ 1. 600003 测试丙 30.00 +4.0% 120分位 50% 得分 50.0
'''
        result = parse_selection(text, 'yaogu-scan.py')
        self.assertEqual(['pass', 'watch', 'reserve'], [x['group'] for x in result['candidates']])
        self.assertEqual(4, result['groups']['watch']['total'])
        self.assertEqual(1, result['groups']['watch']['shown'])
        stock = result['candidates'][0]
        self.assertEqual(9.5, stock['stop'])
        self.assertEqual(100, stock['shares'])
        self.assertIn('成交量为平时的3.0倍', stock['reason'])

    def test_tests_and_unstructured_reports_are_excluded(self):
        self.assertIsNone(parse_selection('冒烟测试\n┌─ A级 (1 只)', 'yaogu-scan.py'))
        self.assertIsNone(parse_selection('A级硬闸门通过', 'yaogu-scan.py'))
        self.assertIsNone(parse_selection('┌─ A级 (1 只)', 'unknown.py'))

    def test_hong_kong_rows_do_not_invent_prices_or_targets(self):
        text = '''代码 名称 板块 涨幅% 60分% 20分% 套牢% 上影% 下影% 量比 ATR% 2ATR止损 成交亿 量形态 判定
┌─ ⚪ 观察（有瑕疵）
01347 测试港股 半导体 0.94 24 53 64 24.2 37.9 0.00 9.4 60.11 54.27 量能缺失 ⚠ 人工复核
┌─ 🔴 否决
其他股票 未通过
'''
        result = parse_selection(text, 'hk-scan.py')
        self.assertEqual(1, len(result['candidates']))
        candidate = result['candidates'][0]
        self.assertEqual('watch', candidate['group'])
        self.assertEqual(60.11, candidate['stop'])
        self.assertNotIn('price', candidate)
        self.assertNotIn('target', candidate)

    def test_empty_group_stays_empty(self):
        result = parse_selection('┌─ A级 (0 只)\n┌─ 🔴 否决\n测试甲 上影过长', 'yaogu-scan.py')
        self.assertEqual([], result['candidates'])
        self.assertEqual(0, result['groups']['pass']['total'])


if __name__ == '__main__':
    unittest.main()
