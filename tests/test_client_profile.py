import tempfile,unittest
from pathlib import Path
from crm import db,services as sv,clientprofile,tasks,comms

class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(str(Path(tempfile.mkdtemp())/'test.sqlite3'));db.init_db(self.con)
        self.user={'id':1,'role':'admin','name':'Админ'}
        self.sid=sv.create_student(self.con,{'name':'Матвей','parent_name':'Анна','phone':'+79001112233'},self.user)
        s=sv.get_student_row(self.con,self.sid);self.fid=s['family_id']
        p=dict(self.con.execute('SELECT * FROM parents WHERE family_id=?',(self.fid,)).fetchone())
        self.body={'name':'Матвей Тестовый','birth':'2021-01-01','note':'Позвонить папе','expected':clientprofile.signature(self.con,self.sid),'parents':[{**p,'is_primary':False},{'name':'Павел','phone':'+79001114455','relation':'папа','email':'','messenger':'@test_parent','is_primary':True}]}
    def test_edit_add_primary_and_no_message(self):
        clientprofile.save(self.con,self.sid,self.body,self.user)
        d=sv.student_detail(self.con,self.sid)
        self.assertEqual(d['student']['name'],'Матвей Тестовый');self.assertEqual(d['parents'][0]['name'],'Павел')
        self.assertEqual(len(d['parents']),2);self.assertEqual(d['family']['note'],'Позвонить папе')
        for phone,ext,ts in [('79001112233','mom','2026-11-13T12:00'),('79001114455','dad','2026-11-12T12:00')]:
            self.con.execute("INSERT INTO conversations(channel,external_id,phone_norm,family_id,last_at,created_at) VALUES ('telegram',?,?,?,?,?)",(ext,phone,self.fid,ts,ts))
        dad=self.con.execute("SELECT id FROM conversations WHERE external_id='dad'").fetchone()[0]
        self.assertEqual(tasks.best_conversation(self.con,self.fid),dad)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        with self.assertRaises(sv.ApiError):clientprofile.save(self.con,self.sid,self.body,self.user)
    def test_imported_erp_status_is_preserved(self):
        self.con.execute("UPDATE students SET erp_status='Постоянный посетитель' WHERE id=?",(self.sid,))
        body={**self.body,'expected':clientprofile.signature(self.con,self.sid),'stage':'no_answer'}
        with self.assertRaises(sv.ApiError):clientprofile.save(self.con,self.sid,body,self.user)
        self.assertEqual(sv.get_student_row(self.con,self.sid)['erp_status'],'Постоянный посетитель')
        self.assertEqual(sv.get_student_row(self.con,self.sid)['name'],'Матвей')

    def test_reject_foreign_contact_and_roll_back(self):
        other=sv.create_student(self.con,{'name':'Другой','parent_name':'Мария','phone':'+79001119988'},self.user)
        pid=self.con.execute('SELECT id FROM parents WHERE family_id=(SELECT family_id FROM students WHERE id=?)',(other,)).fetchone()[0]
        body={**self.body,'parents':[self.body['parents'][0],{**self.body['parents'][1],'id':pid}]}
        with self.assertRaises(sv.ApiError):clientprofile.save(self.con,self.sid,body,self.user)
        self.assertEqual(sv.get_student_row(self.con,self.sid)['name'],'Матвей')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM parents WHERE family_id=?',(self.fid,)).fetchone()[0],1)
    def test_primary_without_matching_chat_does_not_use_other_parent(self):
        clientprofile.save(self.con,self.sid,self.body,self.user)
        self.con.execute("INSERT INTO conversations(channel,external_id,phone_norm,family_id,created_at) VALUES ('telegram','mom','79001112233',?,'2026-11-13')",(self.fid,))
        self.assertIsNone(tasks.best_conversation(self.con,self.fid))

if __name__=='__main__':unittest.main()
