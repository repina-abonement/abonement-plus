import unittest,tempfile
from pathlib import Path
from crm import db,services as sv,studentdelete

class ErroneousClientDelete(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.con=db.connect(str(Path(self.tmp.name)/'delete.sqlite3'));self.addCleanup(self.con.close);db.init_db(self.con)
        self.user={'id':1,'role':'admin','name':'Synthetic'}
        self.sid=sv.create_new_lead(self.con,{'name':'Synthetic Error','parent_name':'Synthetic Parent','phone':'+79000000101'},self.user)

    def test_delete_clean_error_keeps_family_and_sibling_and_audit(self):
        student=sv.get_student_row(self.con,self.sid)
        other=sv.create_new_lead(self.con,{'name':'Synthetic Sibling','family_id':student['family_id']},self.user)
        q=studentdelete.preview(self.con,self.sid,self.user)
        self.assertTrue(q['allowed'],q['blockers'])
        studentdelete.delete(self.con,self.sid,self.user,{'token':q['token'],'category':'test','reason':'Synthetic erroneous card','confirmed_error':True})
        self.assertIsNone(self.con.execute('SELECT 1 FROM students WHERE id=?',(self.sid,)).fetchone())
        self.assertIsNotNone(self.con.execute('SELECT 1 FROM students WHERE id=?',(other,)).fetchone())
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM parents').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM deleted_client_cards').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks WHERE student_id=?',(self.sid,)).fetchone()[0],0)

    def test_refusal_is_not_a_deletion_reason(self):
        q=studentdelete.preview(self.con,self.sid,self.user)
        with self.assertRaises(sv.ApiError):studentdelete.delete(self.con,self.sid,self.user,{'token':q['token'],'category':'refused','reason':'Client stopped attending','confirmed_error':True})
        self.assertIsNotNone(self.con.execute('SELECT 1 FROM students WHERE id=?',(self.sid,)).fetchone())

    def test_finance_blocks_delete_and_other_operator_denied(self):
        self.con.execute("INSERT INTO payments(student_id,amount,method,paid_at,created_at) VALUES (?,950,'card_branch','2026-11-18','2026-11-18')",(self.sid,))
        q=studentdelete.preview(self.con,self.sid,self.user)
        self.assertFalse(q['allowed'])
        with self.assertRaises(sv.ApiError):studentdelete.delete(self.con,self.sid,self.user,{'token':q['token'],'category':'duplicate','reason':'Synthetic duplicate','confirmed_error':True})
        with self.assertRaises(sv.ApiError):studentdelete.preview(self.con,self.sid,{'id':999,'role':'operator'})
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM payments').fetchone()[0],950)
