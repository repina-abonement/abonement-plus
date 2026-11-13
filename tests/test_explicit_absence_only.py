import tempfile,unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from crm import db,services as sv,schedule,clientattendance

class ExplicitAbsenceTest(unittest.TestCase):
    def test_past_unmarked_is_not_absence_but_explicit_future_is(self):
        con=db.connect(str(Path(tempfile.mkdtemp())/'test.sqlite3'));db.init_db(con)
        u={'id':1,'name':'Тест','role':'admin'}
        b=schedule.save_branch(con,{'name':'Тест'});g=schedule.save_group(con,{'name':'Вт/Вс','branch_id':b,'weekdays':[1,6],'time':'17:00'})
        sid=sv.create_student(con,{'name':'Тестовый Ребёнок','parent_name':'Анна','phone':'+79000000331','funnel':'members','group_id':g},u)
        con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) SELECT ?,id,'2026-11','fixed',9,1000,9000,'paid','2026-11-01' FROM enrollments WHERE student_id=?",(sid,sid))
        with patch('crm.services.today',return_value=date(2026,11,13)):
            first=clientattendance.calendar(con,sid,'2026-11')
            self.assertTrue(any(e['date']<'2026-11-13' and e['status']=='planned' for e in first['events']))
            self.assertEqual(first['counters']['available'],0)
            con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-11-10','present')",(sid,g))
            con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-11-08','absent')",(sid,g))
            con.execute("INSERT INTO attendance(student_id,group_id,date,status,warned) VALUES (?,?,'2026-11-15','absent',1)",(sid,g))
            result=clientattendance.calendar(con,sid,'2026-11')
            self.assertEqual(result['counters']['available'],2)
            self.assertEqual(result['counters']['available_future'],1)
            self.assertEqual({r['date'] for r in result['counters']['available_ids']},{'2026-11-08','2026-11-15'})
            sv.mark_attendance(con,sid,g,'2026-11-08','clear',u)
            self.assertEqual(sv.absence_counters(con,sid)['available'],1)

if __name__=='__main__':unittest.main()
