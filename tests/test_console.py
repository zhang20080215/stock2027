import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.collector import redact, sync
from app.server import Application, handler, password_hash
from app.store import connect


class ConsoleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / 'workspace'
        self.sessions = self.base / 'sessions'
        self.sessions.mkdir()
        (self.workspace / 'memory').mkdir(parents=True)
        self.scan_dir = self.workspace / 'skills/a-share-short-term-analysis/scans'
        self.scan_dir.mkdir(parents=True)
        self.db = str(self.base / 'data/console.db')
        self.scan_name = 'scan-a-20261124-1000.json'
        (self.scan_dir / self.scan_name).write_text(json.dumps({'stamp':'20261124-1000','universe':{'sh600001':{'code':'sh600001','name':'测试股票','price':10}},'kline':{},'industry':[]}), encoding='utf-8')
        (self.workspace / 'positions.json').write_text('[]')
        (self.sessions / 'sessions.json').write_text(json.dumps({'wx':{'sessionId':'session-1','deliveryContext':{'channel':'openclaw-weixin'},'usageFamilySessionIds':['session-old']}}))
        self.log = self.sessions / 'session-1.jsonl'
        self.events = [
            {'type':'session','id':'session-1'},
            self.message('u','user','选股测试', '2026-11-24T01:00:00Z'),
            self.message('a','assistant',[{'type':'thinking','thinking':'hidden-reasoning'},{'type':'text','text':'测试结论 API_KEY=sk-1234567890123456'},{'type':'toolCall','id':'call1','name':'exec','arguments':{'command':'python3 templates/a-scan.py'}}]),
            self.message('r','toolResult',[{'type':'text','text':'报告 '+self.scan_name}],toolCallId='call1',details={'exitCode':0,'status':'completed'}),
            self.message('secret','toolResult',[{'type':'text','text':'do-not-import-tool-secret'}],toolCallId='other'),
        ]
        self.write_log()

    def message(self, event_id, role, content, stamp='2026-11-24T02:00:00Z', **extra):
        return {'type':'message','id':event_id,'timestamp':stamp,'message':{'role':role,'content':content,**extra}}

    def write_log(self):
        self.log.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in self.events)+'\n',encoding='utf-8')

    def collect(self):
        return sync(self.db,self.workspace,self.sessions)

    def test_sync_is_idempotent_and_excludes_internal_content(self):
        self.collect()
        self.collect()
        db=connect(self.db)
        self.addCleanup(db.close)
        messages=list(db.execute('SELECT * FROM messages'))
        self.assertEqual(2,len(messages))
        self.assertNotIn('hidden-reasoning',str([dict(x) for x in messages]))
        self.assertNotIn('sk-1234567890123456',messages[1]['text'])
        self.assertEqual('completed',db.execute('SELECT status FROM tasks').fetchone()[0])
        self.assertEqual('session-1',db.execute('SELECT session_id FROM scans').fetchone()[0])
        self.assertEqual(1,db.execute('SELECT count(*) FROM reports').fetchone()[0])
        self.assertNotIn('do-not-import-tool-secret',db.execute('SELECT text FROM reports').fetchone()[0])

    def test_project_memory_rules_and_scheduler_boundaries(self):
        from unittest.mock import patch
        (self.workspace / 'MEMORY.md').write_text('长期约定 API_KEY=private-value', encoding='utf-8')
        (self.workspace / 'HEARTBEAT.md').write_text('<!-- no tasks -->', encoding='utf-8')
        (self.workspace / 'TOOLS.md').write_text('private-tool-config', encoding='utf-8')
        (self.workspace / 'memory/daily.md').write_text('每日记录', encoding='utf-8')
        cron = self.base / 'jobs.json'
        cron.write_text(json.dumps({'jobs': [
            {'agentId': self.sessions.parent.name, 'id': 'mine', 'enabled': True, 'schedule': {'kind': 'cron', 'expr': '0 9 * * 1-5'}, 'payload': {'message': 'not-archived'}},
            {'agentId': 'another-agent', 'id': 'other'}]}), encoding='utf-8')
        with patch.dict('os.environ', {'STOCK_CRON': str(cron)}):
            self.collect()
        app = Application(self.db, '')
        self.assertEqual(2, len(app.api('documents', {})))
        self.assertNotIn('private-value', app.api('documents', {'id': ['MEMORY.md']})['text'])
        project = app.api('project', {})
        self.assertEqual(['HEARTBEAT.md'], [r['id'] for r in project['rules']])
        self.assertEqual(['mine'], [j['id'] for j in project['scheduler']['items']])
        self.assertNotIn('not-archived', json.dumps(project))
        cron.write_text('{broken', encoding='utf-8')
        with patch.dict('os.environ', {'STOCK_CRON': str(cron)}):
            self.collect()
        self.assertEqual('unknown', app.api('project', {})['scheduler']['status'])

    def test_append_partial_line_then_complete_and_archive_deduplicates(self):
        self.collect()
        with self.log.open('a',encoding='utf-8') as f:
            f.write('{"type":')
        self.collect()
        self.write_log()
        with self.log.open('a',encoding='utf-8') as f:
            f.write(json.dumps(self.message('u2','user','新增消息'))+'\n')
        self.log.with_name('session-1.jsonl.reset.2026-11-24').write_bytes(self.log.read_bytes())
        self.collect()
        db=connect(self.db);self.addCleanup(db.close)
        self.assertEqual(3,db.execute('SELECT count(*) FROM messages').fetchone()[0])

    def test_hong_kong_date_filter_and_literal_search(self):
        self.events.append(self.message('late','user','100%_safe','2026-11-23T16:01:00Z'))
        self.write_log();self.collect()
        app=Application(self.db,password_hash('correct-password'))
        result=app.api('messages',{'date':['2026-11-24'],'q':['100%_']})
        self.assertEqual(1,result['total'])
        self.assertEqual('100%_safe',result['items'][0]['text'])

    def test_bad_source_preserves_previous_data_and_reports_error(self):
        self.collect()
        (self.workspace / 'positions.json').write_text('{bad')
        info=self.collect()
        self.assertTrue(any('positions.json' in x for x in info['errors']))
        self.assertEqual([],Application(self.db,'').api('positions',{})['items'])

    def test_auth_cookie_readonly_api_and_origin_protection(self):
        self.collect()
        app=Application(self.db,password_hash('correct-password'))
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(app))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url='http://127.0.0.1:'+str(server.server_port)
        with self.assertRaises(HTTPError) as err:urlopen(url+'/api/positions')
        self.assertEqual(401,err.exception.code)
        req=Request(url+'/api/login',json.dumps({'password':'correct-password'}).encode(),{'Content-Type':'application/json'})
        with urlopen(req) as response:
            cookie=response.headers['Set-Cookie']
        self.assertIn('HttpOnly',cookie)
        with urlopen(Request(url+'/api/overview',headers={'Cookie':cookie})) as response:
            self.assertEqual(2,json.load(response)['counts']['messages'])
        with self.assertRaises(HTTPError) as err:
            urlopen(Request(url+'/api/login',b'{}',{'Origin':'https://evil.example'}))
        self.assertEqual(403,err.exception.code)
        with self.assertRaises(HTTPError) as err:urlopen(Request(url+'/api/scan',b'{}',{'Cookie':cookie}))
        self.assertEqual(405,err.exception.code)
        with self.assertRaises(HTTPError) as err:urlopen(Request(url+'/.env',headers={'Cookie':cookie}))
        self.assertEqual(404,err.exception.code)

    def test_auth_rate_limit_and_secret_redaction(self):
        app=Application(self.db,password_hash('correct-password'))
        for _ in range(10):self.assertEqual(401,app.login('wrong','ip')[1])
        self.assertEqual(429,app.login('correct-password','ip')[1])
        text='-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----\nTUSHARE_TOKEN=12345'
        self.assertNotIn('secret',redact(text))
        self.assertNotIn('12345',redact(text))

    def test_source_code_reads_are_not_scan_reports(self):
        self.events.extend([
            self.message('read-call','assistant',[{'type':'toolCall','id':'read-call','name':'exec','arguments':{'command':'cat scripts/yaogu-scan.py'}}]),
            self.message('read-result','toolResult','source code, not a scan',toolCallId='read-call'),
        ])
        self.write_log();self.collect()
        db=connect(self.db);self.addCleanup(db.close)
        self.assertEqual(1,db.execute('SELECT count(*) FROM reports').fetchone()[0])

    def test_web_database_connection_rejects_mutations(self):
        self.collect()
        import sqlite3
        db=connect(self.db,readonly=True);self.addCleanup(db.close)
        with self.assertRaises(sqlite3.OperationalError):
            db.execute('DELETE FROM messages')

    def test_task_status_filter_and_pagination(self):
        self.collect()
        app=Application(self.db,'')
        self.assertEqual(0,app.api('tasks',{'status':['failed']})['total'])
        self.assertEqual(1,app.api('tasks',{'status':['completed']})['total'])
        self.assertEqual([],app.api('tasks',{'offset':['1']})['items'])

    def test_message_cursor_handles_equal_times_and_new_messages(self):
        self.events += [self.message('tie1','user','same time one'),self.message('tie2','assistant','same time two')]
        self.write_log();self.collect()
        app=Application(self.db,'')
        first=app.api('messages',{'limit':['2']})
        self.assertTrue(first['has_more'])
        cursor=first['items'][-1]
        self.events.append(self.message('new','user','new arrival','2026-11-24T03:00:00Z'))
        self.write_log();self.collect()
        second=app.api('messages',{'limit':['2'],'before_id':[cursor['id']],'before_time':[cursor['timestamp']]})
        self.assertFalse(second['has_more'])
        ids=[m['id'] for m in first['items']+second['items']]
        self.assertEqual(4,len(set(ids)))
        self.assertNotIn('session-1:new',ids)
        self.assertIn('session-1:u',ids)

    def test_date_list_groups_by_hong_kong_day(self):
        self.events += [self.message('midnight','user','after midnight','2026-11-23T16:01:00Z'), self.message('previous','user','previous day','2026-11-23T15:59:00Z')]
        self.write_log();self.collect()
        app=Application(self.db,'')
        days=app.api('message-days',{})
        self.assertEqual(['2026-11-24','2026-11-23'],[x['day'] for x in days])
        self.assertEqual(3,days[0]['count'])
        self.assertEqual([],app.api('message-days',{'session':['other-session']}))

    def test_search_target_has_full_context_and_bidirectional_history(self):
        self.events += [self.message('context-%02d'%i,'user' if i%2 else 'assistant','context message %d'%i,'2026-11-24T04:%02d:00Z'%i) for i in range(60)]
        self.write_log();self.collect()
        app=Application(self.db,'')
        data=app.api('message-context',{'id':['session-1:context-30']})
        self.assertEqual('2026-11-24',data['date'])
        self.assertEqual(41,len(data['items']))
        self.assertTrue(data['has_before']);self.assertTrue(data['has_after'])
        self.assertIn('session-1:context-30',[m['id'] for m in data['items']])
        latest=data['items'][0]
        newer=app.api('messages',{'date':[data['date']],'after_id':[latest['id']],'after_time':[latest['timestamp']]})
        self.assertEqual('session-1:context-59',newer['items'][0]['id'])
        self.assertFalse(newer['has_more'])
        self.assertFalse(set(m['id'] for m in data['items'])&set(m['id'] for m in newer['items']))
        self.assertIsNone(app.api('message-context',{'id':['session-1:context-30'],'session':['other-session']}))
        self.assertIsNone(app.api('message-context',{'id':['missing']}))

    def test_model_final_reply_is_stored_and_replaced_per_request(self):
        self.events[1]['message']['content']='全局选股'
        self.events[2]['parentId']='u'
        self.events[2]['message']['stopReason']='toolUse'
        self.events[3]['parentId']='a'
        final=self.message('final','assistant','**推荐：测试乙600002**\n- 买入：20–21\n- 止损：18',stopReason='stop',provider='deepseek',model='test-model')
        final['parentId']='r'
        self.events.append(final);self.write_log();self.collect();self.collect()
        app=Application(self.db,'')
        records=app.api('model-results',{})
        self.assertEqual(1,len(records))
        result=app.api('model-result',{'id':[records[0]['id']]})
        self.assertEqual('session-1:u',result['user_message_id'])
        self.assertEqual('600002',result['data']['stocks'][0]['code'])
        self.assertEqual('20–21',result['data']['stocks'][0]['entry'])
        self.assertEqual('test-model',result['model'])
        last=self.message('final2','assistant','没有合适股票，继续空仓。','2026-11-24T03:00:00Z',stopReason='stop')
        last['parentId']='final';self.events.append(last);self.write_log();self.collect()
        records=app.api('model-results',{})
        self.assertEqual(1,len(records))
        self.assertEqual('session-1:final2',records[0]['id'])


if __name__=='__main__':
    unittest.main()
