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

    def test_trial_group_directory_is_independent_and_excludes_unavailable_groups(self):
        other_branch=self.con.execute("INSERT INTO branches(name,active) VALUES ('Другой',1)").lastrowid
        disabled=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,age,trial_ok) VALUES (?,'Без пробных','1','12:00','3–5',0)",(other_branch,)).lastrowid
        makeup=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,age,is_makeup) VALUES (?,'Отработка','1','12:00','3–5',1)",(other_branch,)).lastrowid
        rows=trialoffer.booking_groups(self.con,self.sid)['groups']
        self.assertEqual([row['id'] for row in rows],[self.gid])
        self.assertNotIn(disabled,[row['id'] for row in rows])
        self.assertNotIn(makeup,[row['id'] for row in rows])
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
    def test_member_trial_preserves_main_group_and_funnel(self):
        home=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,age) VALUES (?,'Основная','0','10:00','3–5')",(self.bid,)).lastrowid
        self.con.execute("UPDATE students SET funnel='members',stage='active',group_id=? WHERE id=?",(home,self.sid))
        with patch('crm.tasks.send_booked'):
            result=trialoffer.book_inline(self.con,self.sid,self.data,self.user)
        self.assertTrue(result['ok'])
        row=sv.get_student_row(self.con,self.sid)
        self.assertEqual((row['group_id'],row['trial_group_id'],row['funnel']),(home,self.gid,'members'))
        self.assertEqual(row['trial_at'],self.day.isoformat()+'T16:45')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],0)

    def test_calendar_offers_schedule_times_and_rejects_manual_time(self):
        self.con.execute("UPDATE students SET name='Даниил Примеров',birth=? WHERE id=?",(self.birth,self.sid))
        result=trialoffer.calendar_slots(self.con,self.sid,self.gid,self.day.isoformat()[:7])
        slot=next(x for x in result['slots'] if x['date']==self.day.isoformat())
        self.assertEqual(slot['time'],'16:45')
        with self.assertRaises(sv.ApiError):
            trialoffer.book_calendar(self.con,self.sid,{'group_id':self.gid,'date':self.day.isoformat(),'time':'15:01'},self.user)
        with patch('crm.tasks.send_booked'):
            trialoffer.book_calendar(self.con,self.sid,{**slot,'request_id':'calendar-future-123'},self.user)
        self.assertEqual(sv.get_student_row(self.con,self.sid)['trial_at'],self.day.isoformat()+'T16:45')

    def test_calendar_past_event_without_client_fields_or_money(self):
        from crm import historyentry
        self.con.execute("UPDATE students SET name='Даниил Примеров',birth=? WHERE id=?",(self.birth,self.sid))
        day=self.day-timedelta(days=14)
        result=trialoffer.calendar_slots(self.con,self.sid,self.gid,day.isoformat()[:7])
        slot=next(x for x in result['slots'] if x['date']==day.isoformat())
        self.assertTrue(slot['past'])
        self.assertFalse(any(s['past'] for s in trialoffer.calendar_slots(self.con,self.sid,self.gid,day.isoformat()[:7],future_only=True)['slots']))
        with self.assertRaises(sv.ApiError):
            trialoffer.book_calendar(self.con,self.sid,{**slot,'future_only':True,'request_id':'reject-past-123'},self.user)
        data={**slot,'request_id':'calendar-past-123'}
        trialoffer.book_calendar(self.con,self.sid,data,self.user)
        trialoffer.book_calendar(self.con,self.sid,data,self.user)
        student=sv.get_student_row(self.con,self.sid)
        self.assertEqual(student['trial_at'],day.isoformat()+'T16:45')
        self.assertEqual(student['trial_came'],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM historical_entries').fetchone()[0],1)

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

    def test_admin_calendar_can_choose_another_age_group_and_still_checks_real_slot(self):
        other=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,age,capacity,trial_ok) VALUES (?,'Другая возрастная группа',?,'18:15','8–10',10,1)",(self.bid,str(self.day.weekday()))).lastrowid
        smart.fill_required(self.con,self.sid,{'child_name':'Даниил Примеров','birth':self.birth,
            'parent_name':'Екатерина','phone':'+79000000001'},self.user)
        result=trialoffer.calendar_slots(self.con,self.sid,other,self.day.isoformat()[:7],future_only=True)
        slot=next(x for x in result['slots'] if x['date']==self.day.isoformat())
        with patch('crm.tasks.send_booked'):
            trialoffer.book_calendar(self.con,self.sid,{**slot,'future_only':True,'request_id':'manual-other-group'},self.user)
        row=sv.get_student_row(self.con,self.sid)
        self.assertEqual((row['trial_group_id'],row['trial_at']),(other,self.day.isoformat()+'T18:15'))

    def test_two_year_old_books_youngest_group_and_rebooking_keeps_first_visit(self):
        self.con.execute("UPDATE groups SET age='3–4' WHERE id=?",(self.gid,))
        birth=(self.day.replace(year=self.day.year-3)+timedelta(days=1)).isoformat()
        data={**self.data,'birth':birth}
        self.assertEqual(len(trialoffer.booking_options(self.con,self.sid,birth,self.day.isoformat())['slots']),1)
        with patch('crm.tasks.send_booked'):
            trialoffer.book_inline(self.con,self.sid,data,self.user)
        sv.mark_trial(self.con,self.sid,True,self.user)
        first_history=self.con.execute("SELECT COUNT(*) FROM history WHERE student_id=? AND text='Пришёл на пробное'",(self.sid,)).fetchone()[0]
        second=self.day+timedelta(days=7)
        self.con.execute("UPDATE groups SET weekdays=? WHERE id=?",(str(second.weekday()),self.gid))
        with patch('crm.tasks.send_booked'):
            trialoffer.book_inline(self.con,self.sid,{**data,'date':second.isoformat()},self.user)
        student=sv.get_student_row(self.con,self.sid)
        self.assertEqual((student['birth'],student['trial_at'],student['trial_came']),(birth,second.isoformat()+'T16:45',None))
        first=self.con.execute('SELECT visit_at,came,source_id FROM erp_trial_visits WHERE student_id=?',(self.sid,)).fetchone()
        self.assertEqual((first['visit_at'],first['came'],first['source_id']),(self.day.isoformat()+'T16:45',1,None))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM history WHERE student_id=? AND text='Пришёл на пробное'",(self.sid,)).fetchone()[0],first_history)


if __name__=='__main__':unittest.main()
