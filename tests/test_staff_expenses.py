import unittest,base64
from crm import staffexpenses as ex,perms
from tests.test_planner_shifts import PlannerShifts

class StaffExpenses(unittest.TestCase):
    setUp=PlannerShifts.setUp
    def owner(self):return dict(self.con.execute("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone())
    def create(self,source='personal',request='synthetic-batch-1'):
        return ex.create_batch(self.con,self.offline,{'request_id':request,'defaults':{'paid_by_id':self.offline['id'],'paid_date':'2026-11-18','funding':source},'rows':[{'title':'Synthetic purchase','amount':'1000.50'}]})['ids'][0]
    def test_batch_parser_total_and_idempotent_save(self):
        parsed=ex.parse('Покупка А 2288\nПокупка Б (упаковка) 212\nПокупка В 135\nПечать 396\nКрепления 262\nСтирка 800\n\n4800 - работа техника\n264 - печать')
        self.assertEqual(parsed['total_minor'],915700);self.assertEqual(len(parsed['rows']),8);self.assertEqual(parsed['errors'],[])
        self.assertEqual(ex.parse('Покупка 2 288,50 ₽')['total_minor'],228850)
        self.assertTrue(ex.parse('непонятная строка')['errors'])
        self.assertTrue(ex.parse('Итого 9157')['warnings'])
        first=self.create();self.assertEqual(self.create(),first)
        self.assertEqual(len(ex.listing(self.con,self.varya)['rows']),1)
        row=ex.listing(self.con,self.varya)['rows'][0];self.assertEqual(row['created_by'],self.offline['id']);self.assertEqual(row['paid_by_id'],self.offline['id'])
    def test_partial_reimbursement_only_owner_and_actual_payer_confirmation(self):
        eid=self.create()
        payload={'amount':'400','transferred_at':'2026-11-18','method':'transfer','request_id':'synthetic-transfer-1'}
        with self.assertRaises(ex.ExpenseError):ex.reimburse(self.con,self.varya,eid,payload)
        with self.assertRaises(ex.ExpenseError):ex.reimburse(self.con,self.owner(),eid,payload)
        # Preserve an old partial reimbursement; a repeat of that saved request is harmless.
        rid=self.con.execute("INSERT INTO staff_reimbursements(expense_id,amount,transferred_at,method,recorded_by,recorded_at,request_id) VALUES(?,40000,'2026-11-18','transfer',?,'2026-11-18',?)",(eid,self.owner()['id'],payload['request_id'])).lastrowid
        transfer={'id':rid}

        self.assertEqual(ex.reimburse(self.con,self.owner(),eid,payload),transfer)
        with self.assertRaises(ex.ExpenseError):ex.receipt_money(self.con,self.varya,transfer['id'],{'received':True})
        ex.receipt_money(self.con,self.offline,transfer['id'],{'received':True})
        first=ex.listing(self.con,self.offline)['rows'][0];self.assertEqual(first['received'],40000);self.assertEqual(first['to_transfer'],60050)
        second=ex.reimburse(self.con,self.owner(),eid,dict(payload,amount='600.50',request_id='synthetic-transfer-2'))
        pending=ex.listing(self.con,self.varya)['rows'][0];self.assertEqual(pending['awaiting_receipt'],0);self.assertEqual(pending['to_transfer'],0)
        ex.receipt_money(self.con,self.offline,second['id'],{'received':True});ex.receipt_money(self.con,self.offline,second['id'],{'received':True})
        final=ex.listing(self.con,self.varya)['rows'][0];self.assertEqual(final['status'],'Возмещено')
        self.assertEqual(len([h for h in final['history'] if h['action']=='money_received']),2)
        with self.assertRaises(ex.ExpenseError):ex.reimburse(self.con,self.owner(),eid,dict(payload,request_id='synthetic-transfer-3'))
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],0)
    def test_cash_no_debt_and_permissions(self):
        self.create('cash')
        result=ex.listing(self.con,self.varya);self.assertEqual(result['summary']['to_transfer'],0)
        with self.assertRaises(ex.ExpenseError):ex.listing(self.con,self.other)
        self.assertEqual(result['funding']['cash'],'Конверт')
        self.assertTrue(perms.can(self.offline,'staffexpenses'))
        self.assertTrue(perms.can(self.varya,'staffexpenses'))
        self.assertFalse(perms.can(self.other,'staffexpenses'))
    def test_audit_conflicting_edit_and_receipt(self):
        eid=self.create();row=ex.listing(self.con,self.offline)['rows'][0]
        edit={'title':'Corrected','amount':'1000.50','paid_date':'2026-11-18','paid_by_id':self.offline['id'],'funding':'personal','version':1}
        ex.edit(self.con,self.varya,eid,edit)
        with self.assertRaises(ex.ExpenseError):ex.edit(self.con,self.offline,eid,edit)
        photo=ex.add_receipt(self.con,self.offline,eid,{'data':base64.b64encode(b'\x89PNG\r\n\x1a\nsynthetic').decode()})
        self.assertTrue(photo['id']);self.assertEqual(len(ex.listing(self.con,self.varya)['rows'][0]['receipts']),1)
        with self.assertRaises(ex.ExpenseError):ex.add_receipt(self.con,self.offline,eid,{'data':base64.b64encode(b'<script>bad</script>').decode()})
        transfer=ex.reimburse(self.con,self.owner(),eid,{'amount':'1000.50','transferred_at':'2026-11-18','method':'cash','request_id':'synthetic-transfer-1'})
        with self.assertRaises(ex.ExpenseError):ex.edit(self.con,self.varya,eid,dict(edit,version=2,amount='900'))
        ex.receipt_money(self.con,self.owner(),transfer['id'],{'voided':True})
        ex.edit(self.con,self.varya,eid,{'version':2,'cancelled':True})
        self.assertEqual(ex.listing(self.con,self.offline)['rows'][0]['status'],'Отменён')
    def test_real_http_permissions_records_and_private_receipt(self):
        import json,threading,urllib.request,urllib.error
        from datetime import datetime
        from unittest.mock import patch
        from crm import db,server
        actors=[self.offline,self.varya,self.other,self.owner()]
        for actor in actors:self.con.execute('INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)',(f"synthetic-expense-{actor['id']}",actor['id'],datetime.now().isoformat()))
        path=self.con.execute('PRAGMA database_list').fetchone()[2]
        with patch.object(db,'DB_PATH',path):
            srv=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
            def call(actor,method,url,data=None,raw=False):
                req=urllib.request.Request(f'http://127.0.0.1:{srv.server_port}'+url,method=method,data=json.dumps(data).encode() if data is not None else None,headers={'Cookie':f"crm_session=synthetic-expense-{actor['id']}",'Content-Type':'application/json'})
                try:
                    with urllib.request.urlopen(req,timeout=5) as r:return r.status,r.read() if raw else json.load(r)
                except urllib.error.HTTPError as e:return e.code,json.load(e)
            try:
                event_code,event=call(self.offline,'POST','/api/planner/events',{'title':'Synthetic party','day':'2026-11-28','start_time':'18:00','end_time':'19:00','participants':'Synthetic participants'})
                self.assertEqual(event_code,200)
                event_day=call(self.varya,'GET',f'/api/planner/week?rolling=1&days=1&start=2026-11-28&user={self.pool}')[1]['days'][0]
                self.assertEqual(event_day['events'][0]['id'],event['id']);self.assertEqual(event_day['events'][0]['end_time'],'19:00')
                self.assertEqual(call(self.other,'GET','/api/staff-expenses')[0],403)
                self.assertEqual(call(self.offline,'GET','/api/staff-expenses')[0],200)
                code,result=call(self.offline,'POST','/api/staff-expenses',{'request_id':'http-batch-1','defaults':{'paid_by_id':self.offline['id'],'funding':'personal','paid_date':'2026-11-18'},'rows':[{'title':'Synthetic','amount':100}]})
                self.assertEqual(code,200);eid=result['ids'][0]
                self.assertEqual(call(self.varya,'GET','/api/staff-expenses')[1]['rows'][0]['id'],eid)
                self.assertEqual(call(self.offline,'POST',f'/api/staff-expenses/{eid}/reimburse',{'amount':100,'transferred_at':'2026-11-18','method':'cash','request_id':'http-transfer-1'})[0],400)
                code,photo=call(self.offline,'POST',f'/api/staff-expenses/{eid}/receipts',{'data':base64.b64encode(b'\xff\xd8\xffsynthetic').decode()})
                self.assertEqual(code,404)
                photo=ex.add_receipt(self.con,self.offline,eid,{'data':base64.b64encode(b'\xff\xd8\xffsynthetic').decode()})
                self.assertEqual(call(self.varya,'GET',f"/api/staff-expense-receipts/{photo['id']}",raw=True)[1],b'\xff\xd8\xffsynthetic')
                self.assertEqual(call(self.other,'GET',f"/api/staff-expense-receipts/{photo['id']}")[0],403)
                self.assertEqual(call(self.offline,'POST',f'/api/staff-expenses/{eid}/received',{'request_id':'http-employee-received'})[0],200)
                simplified=call(self.varya,'GET','/api/staff-expenses')[1]['rows'][0]
                self.assertEqual(simplified['status'],'Возмещено');self.assertEqual(simplified['to_transfer'],0)
                self.assertEqual(call(self.offline,'POST',f'/api/staff-expenses/{eid}/received',{'request_id':'http-employee-received'})[0],200)
            finally:srv.shutdown();srv.server_close();thread.join(timeout=5)

    def test_employee_marks_received_in_one_step_and_can_correct_own_mark(self):
        eid=self.create()
        with self.assertRaises(ex.ExpenseError):ex.mark_received(self.con,self.varya,eid,{'request_id':'received-test-1'})
        first=ex.mark_received(self.con,self.offline,eid,{'request_id':'received-test-1'})
        again=ex.mark_received(self.con,self.offline,eid,{'request_id':'received-test-1'})
        self.assertEqual(first,again)
        row=ex.listing(self.con,self.varya)['rows'][0]
        self.assertEqual(row['status'],'Возмещено');self.assertEqual(row['awaiting_receipt'],0)
        self.assertEqual(row['transfers'][0]['recorded_by'],self.offline['id'])
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],0)
        ex.receipt_money(self.con,self.offline,first['id'],{'voided':True})
        self.assertEqual(ex.listing(self.con,self.varya)['rows'][0]['status'],'К возмещению')
        with self.assertRaises(ex.ExpenseError):ex.mark_received(self.con,self.offline,eid,{'request_id':'received-test-2','amount':'400'})
        self.con.execute("INSERT INTO staff_reimbursements(expense_id,amount,transferred_at,method,recorded_by,recorded_at,request_id) VALUES(?,40000,'2026-11-18','transfer',?,'2026-11-18','legacy-partial')",(eid,self.owner()['id']))
        self.assertEqual(ex.listing(self.con,self.varya)['rows'][0]['to_transfer'],60050)
        ex.mark_received(self.con,self.offline,eid,{'request_id':'received-test-3'})
        self.assertEqual(ex.listing(self.con,self.varya)['rows'][0]['status'],'Возмещено')
        self.assertEqual(ex.parse('Печать расписания 264')['rows'][0]['category'],'printing')
        self.assertEqual(ex.parse('Стирка формы 800')['rows'][0]['category'],'laundry')
        self.assertEqual(ex.parse('4800 - работа техника')['rows'][0]['category'],'maintenance')
