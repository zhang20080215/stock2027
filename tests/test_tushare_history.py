import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.tushare_history import read_token, request_minutes


class TushareHistoryTest(unittest.TestCase):
    def test_reads_only_named_token_from_existing_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            path.write_text('OTHER=value\nTUSHARE_TOKEN="synthetic-test-token"\n', encoding='utf-8')
            self.assertEqual('synthetic-test-token', read_token(path))

    def test_https_request_decodes_fields_without_logging_token(self):
        result = {'code': 0, 'data': {'fields': ['trade_time', 'close'], 'items': [['2026-11-23 10:01:00', 20.5]]}}
        with patch('app.tushare_history.urlopen', return_value=io.BytesIO(json.dumps(result).encode())) as mock:
            rows = request_minutes('synthetic-test-token', '600001', '2026-11-23', '2026-11-24')
        req = mock.call_args.args[0]
        self.assertEqual('https://api.tushare.pro', req.full_url)
        self.assertEqual('600001.SH', json.loads(req.data)['params']['ts_code'])
        self.assertEqual(20.5, rows[0]['close'])

    def test_provider_error_message_cannot_leak_credentials(self):
        result = {'code': 40203, 'msg': 'synthetic-test-token denied'}
        with patch('app.tushare_history.urlopen', return_value=io.BytesIO(json.dumps(result).encode())):
            with self.assertRaisesRegex(RuntimeError, '^Tushare error code 40203$'):
                request_minutes('synthetic-test-token', '600001', '2026-11-23')
