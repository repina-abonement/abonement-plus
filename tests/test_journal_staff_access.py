import unittest
from unittest.mock import patch
from tests import test_sale_paid_date as fixture
from crm import perms,server,smart,services as sv


class JournalStaffAccess(unittest.TestCase):
    setUp=fixture.SaleDateTest.setUp

    def test_operators_offline_and_varya_have_full_menu_and_can_mark(self):
        with patch.object(smart,'REQUIRE',False):
            invoice=sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid,paid_date='2026-11-16')
            sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',5120,'2026-11-16','synthetic-access-123')
        accounts=[{'role':'operator','name':'Synthetic Operator'},
                  {'role':'operator','is_offline_admin':1,'name':'Synthetic Offline'},
                  {'role':'operator','is_senior':1,'name':'Synthetic Varya'}]
        for account in accounts:
            user={'id':1,**account}
            self.assertEqual(perms.view_of(user)['journal'],'full')
            self.assertTrue(perms.allowed(user,'journal'))
            server.journal_write(user)
            with patch.object(sv,'today',return_value=__import__('datetime').date(2026,11,18)):
                server.attendance(self.con,None,user,{'student_id':self.sid,'group_id':self.gid,'date':'2026-11-18','status':'present'}, {})
            self.assertEqual(self.con.execute("SELECT status FROM attendance WHERE student_id=? AND date='2026-11-18'",(self.sid,)).fetchone()[0],'present')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0],1)

    def test_unrelated_roles_and_coach_scope_unchanged(self):
        for role in ('lawyer','trainee','offline_pool'):
            self.assertFalse(perms.allowed({'role':role},'journal'))
        self.assertEqual(perms.view_of({'role':'coach'})['journal'],'own')
        self.assertFalse(perms.allowed({'role':'operator'},'fiscal:full'))
