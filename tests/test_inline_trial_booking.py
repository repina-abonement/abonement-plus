"""Booking directly in a client card: correct slots, complete data and atomic failures."""
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from crm import db, services as sv, trialoffer, smart


class InlineBookingTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.con=db.connect(str(Path(self.tmp.name)/'test.sqlite3'));db.init_db(self.con)
        self.user={'id':1,'name':'Тест','role':'admin'}
        self.sid=sv.create_student(self.con,{'name':'Даниил','parent_name':'Екатерина','phone':'+79000000001'},self.user)
        self.day=date.today()+timedelta(days=7);self.birth=date(date.today().year-4,1,1).isoformat()
        self.bid=self.con.execute("INSERT INTO branches(name) VALUES ('Тест')").lastrowid
        self.gid=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,age,capacity,trial_ok) VALUES (?,'Тестовая группа',?,'16:45','3–5',10,1)",(self.bid,str(self.day.weekday()))).lastrowid
        self.data={'child_name':'Даниил Примеров','birth':self.birth,'parent_name':'Екатерина','phone':'+79000000001',
                   'email':'','date':self.day.isoformat(),'time':'16:45','group_id':self.gid}
    def tearDown(self):
        self.con.close();self.tmp.cleanup()
    def test_missing_birth_and_schedule(self):
        self.assertEqual(trialoffer.booking_options(self.con,self.sid)['slots'],[])
        result=trialoffer.booking_options(self.con,self.sid,self.birth,self.day.isoformat())
        self.assertEqual(len(result['slots']),1);self.assertEqual(result['slots'][0]['time'],'16:45')
        self.con.execute("INSERT INTO group_versions(group_id,valid_from,weekdays,time,day_times,created_at) VALUES (?,?,?,'17:30','','2026-11-13')",(self.gid,date.today().isoformat(),str(self.day.weekday())))
        self.assertEqual(trialoffer.booking_options(self.con,self.sid,self.birth,self.day.isoformat())['slots'][0]['time'],'17:30')
    def test_books_with_required_data_in_one_operation(self):
        with patch('crm.tasks.send_booked') as notify:
            result=trialoffer.book_inline(self.con,self.sid,self.data,self.user)
        self.assertTrue(result['ok']);notify.assert_called_once()
        self.con.execute('UPDATE groups SET capacity=1 WHERE id=?',(self.gid,))
        with patch('crm.tasks.send_booked') as again:
            self.assertTrue(trialoffer.book_inline(self.con,self.sid,self.data,self.user)['ok'])
        again.assert_not_called()
        row=sv.get_student_row(self.con,self.sid)
        self.assertEqual(row['name'],'Даниил Примеров');self.assertEqual(row['trial_at'],self.day.isoformat()+'T16:45')
        self.assertEqual(row['group_id'],self.gid);self.assertEqual(smart.required_info(self.con,self.sid,'trial')['missing'],[])
    def test_invalid_details_do_not_leave_partial_booking(self):
        before=dict(sv.get_student_row(self.con,self.sid))
        with self.assertRaises(sv.ApiError):trialoffer.book_inline(self.con,self.sid,{**self.data,'parent_name':'Мама'},self.user)
        self.assertEqual(dict(sv.get_student_row(self.con,self.sid)),before)
        self.assertEqual(self.con.execute('SELECT name FROM parents WHERE family_id=?',(before['family_id'],)).fetchone()[0],'Екатерина')
    def test_rejects_full_group_arbitrary_time_and_past(self):
        with self.assertRaises(sv.ApiError):trialoffer.book_inline(self.con,self.sid,{**self.data,'time':'15:00'},self.user)
        self.con.execute('UPDATE groups SET capacity=1 WHERE id=?',(self.gid,))
        other=sv.create_student(self.con,{'name':'Другой Ребёнок','parent_name':'Анна','phone':'+79000000002'},self.user)
        self.con.execute('UPDATE students SET group_id=?,trial_at=? WHERE id=?',(self.gid,self.day.isoformat()+'T16:45',other))
        self.assertEqual(trialoffer.booking_options(self.con,self.sid,self.birth,self.day.isoformat())['slots'],[])
        with self.assertRaises(sv.ApiError):trialoffer.book_inline(self.con,self.sid,self.data,self.user)
        with self.assertRaises(sv.ApiError):trialoffer.booking_options(self.con,self.sid,self.birth,(date.today()-timedelta(days=1)).isoformat())
        self.assertIsNone(sv.get_student_row(self.con,self.sid)['trial_at'])
    def test_trial_restrictions_and_branch(self):
        self.con.execute('UPDATE groups SET trial_ok=0 WHERE id=?',(self.gid,))
        self.assertEqual(trialoffer.booking_options(self.con,self.sid,self.birth,self.day.isoformat())['slots'],[])
        self.con.execute('UPDATE groups SET trial_ok=1,age=\'8–10\' WHERE id=?',(self.gid,))
        self.assertEqual(trialoffer.booking_options(self.con,self.sid,self.birth,self.day.isoformat())['slots'],[])


if __name__=='__main__':unittest.main()
