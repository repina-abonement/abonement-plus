"""Makeups: paid target month, exact two-month expiry and two paid weekly lessons."""
import tempfile,json
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db,services as sv,schedule,clientattendance,makeuppolicy,smart,capacity


def main():
    con=db.connect(str(Path(tempfile.mkdtemp())/'policy.sqlite3'));db.init_db(con);smart.REQUIRE=False
    u={'id':1,'name':'Test','role':'admin'}
    b=schedule.save_branch(con,{'name':'Test'})
    own=schedule.save_group(con,{'name':'Own Tue Fri','branch_id':b,'weekdays':[1,4],'time':'18:00','capacity':10})
    target=schedule.save_group(con,{'name':'Extra Wednesday','branch_id':b,'weekdays':[2],'time':'12:00','capacity':10})
    sid=sv.create_student(con,{'name':'Synthetic Test','parent_name':'Test','phone':'+79000000177','funnel':'members','group_id':own},u)
    eid=con.execute('SELECT id FROM enrollments WHERE student_id=?',(sid,)).fetchone()[0]
    def invoice(ym,status='paid'):
        return con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,price,per_lesson,status,created_at) VALUES (?,?,?,'fixed',8,8000,1000,?,?)",(sid,eid,ym,status,ym+'-01')).lastrowid
    octid=invoice('2026-10');novid=invoice('2026-11');decid=invoice('2026-12')
    aid=con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-10-06','absent')",(sid,own)).lastrowid
    def fail(fn):
        try:fn();raise AssertionError('unexpected allowed')
        except sv.ApiError:pass
    with patch('crm.services.today',return_value=date(2026,10,7)):
        assert makeuppolicy.check(con,sid,'2026-10-07')==2 # Friday is still in the future.
        assert any(a['id']==aid for a in makeuppolicy.absences(con,sid,'2026-10-07'))
        # October absence remains valid in November, not December, based on target date.
        assert any(a['id']==aid for a in makeuppolicy.absences(con,sid,'2026-11-04'))
        assert not any(a['id']==aid for a in makeuppolicy.absences(con,sid,'2026-12-02'))
        fail(lambda:sv.book_makeup(con,sid,target,'2026-12-02',aid,u))
        con.execute("UPDATE subscriptions SET status='invoiced' WHERE id=?",(novid,))
        fail(lambda:sv.book_makeup(con,sid,target,'2026-11-04',aid,u))
        con.execute("UPDATE subscriptions SET status='paid' WHERE id=?",(novid,))
        # Snapshot of one paid lesson cannot be topped up with a trial/single/makeup.
        one=[{'group_id':own,'dates':['2026-11-03'],'rate':1000}]
        con.execute('UPDATE subscriptions SET historical_sale=1,lesson_schedule=? WHERE id=?',(json.dumps(one),novid))
        fail(lambda:makeuppolicy.check(con,sid,'2026-11-04'))
        fail(lambda:sv.book_makeup(con,sid,target,'2026-11-04',aid,u))
        one[0]['dates'].append('2026-11-06')
        con.execute('UPDATE subscriptions SET lesson_schedule=? WHERE id=?',(json.dumps(one),novid))
        assert makeuppolicy.check(con,sid,'2026-11-04')==2
        with patch('crm.capacity.ensure',return_value=None):
            booked=sv.book_makeup(con,sid,target,'2026-11-04',aid,u)
        assert con.execute('SELECT absence_id FROM makeups WHERE id=?',(booked['id'],)).fetchone()[0]==aid
        # A fourth lesson is allowed: another makeup does not replace the two paid lessons.
        extra=schedule.save_group(con,{'name':'Fourth Saturday','branch_id':b,'weekdays':[5],'time':'13:00','capacity':10})
        aid2=con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-10-09','absent')",(sid,own)).lastrowid
        fourth=sv.book_makeup(con,sid,extra,'2026-11-07',aid2,u)
        assert fourth['id']!=booked['id']
        sv.set_makeup_status(con,fourth['id'],'cancelled',u)
        fail(lambda:sv.book_makeup(con,sid,target,'2026-11-11',aid,u))
        sv.set_makeup_status(con,booked['id'],'cancelled',u)
        assert any(a['id']==aid for a in makeuppolicy.absences(con,sid,'2026-11-04'))
        # Week crossing a month/year boundary uses actual paid dates from both months.
        invoice('2027-01')
        dec=[{'group_id':own,'dates':['2026-12-29'],'rate':1000}]
        jan=[{'group_id':own,'dates':['2027-01-01'],'rate':1000}]
        con.execute('UPDATE subscriptions SET historical_sale=1,composition_corrected=1,lesson_schedule=? WHERE id=?',(json.dumps(dec),decid))
        con.execute("UPDATE subscriptions SET historical_sale=1,composition_corrected=1,lesson_schedule=? WHERE month='2027-01'",(json.dumps(jan),))
        assert makeuppolicy.check(con,sid,'2026-12-30')==2
        con.execute("UPDATE subscriptions SET status='cancelled' WHERE month='2027-01'")
        fail(lambda:makeuppolicy.check(con,sid,'2026-12-30'))
        # Empty past dates never create entitlement.
        assert not makeuppolicy.absences(con,sid,'2027-02-03')
    with patch('crm.services.today',return_value=date(2026,11,1)):
        suggestions=clientattendance.suggestions(con,sid,days=10)
        assert any(s['date']=='2026-11-04' for s in suggestions['slots'])
        con.execute("UPDATE subscriptions SET status='invoiced' WHERE id=?",(novid,))
        assert not clientattendance.suggestions(con,sid,days=10)['slots']
    print('PASS paid target month, two paid scheduled lessons (not two completed), exact snapshot, cross-month week, expiry by target date, API rejects, suggestions match, cancellation restores credit')

if __name__=='__main__':main()
