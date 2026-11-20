"""Обезличенный исторический перенос ERP без финансовых эффектов."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, erphistory, schedule, services, clientattendance, money


def main():
    con=db.connect(os.path.join(tempfile.mkdtemp(),'crm.sqlite3'));db.init_db(con)
    ar=con.execute("INSERT INTO branches(name) VALUES ('Север')").lastrowid
    nb=con.execute("INSERT INTO branches(name) VALUES ('Юг')").lastrowid
    main_group=schedule.save_group(con,{'name':'Сборная Север','branch_id':ar,'weekdays':[1,6],'time':'18:30','day_times':{'6':'19:30'}})
    makeup_groups=[]
    for name,weekdays,tm in [('Доп. 6–8',[3],'19:45'),('ОФП 4–5',[5],'16:00'),('ОФП старшие',[5],'17:00')]:
        makeup_groups.append(schedule.save_group(con,{'name':name,'branch_id':nb,'weekdays':weekdays,'time':tm}))
    fid=con.execute("INSERT INTO families(name) VALUES ('Тестовая семья')").lastrowid
    sid=con.execute("INSERT INTO students(family_id,name,birth,erp_id,stage_changed_at,created_at) VALUES (?,?,?,?,?,?)",
                    (fid,'Тестовый Ребёнок','2015-01-01','TEST-ERP','2026-01-01','2026-01-01')).lastrowid
    con.execute("INSERT INTO enrollments(student_id,group_id,created_at) VALUES (?,?,?)",(sid,main_group,'2025-01-01'))
    duplicate=schedule.save_group(con,{'name':'Сборная Север копия','branch_id':ar,'weekdays':[1,6],'time':'18:30','day_times':{'6':'19:30'}})
    con.execute("UPDATE groups SET active=0 WHERE id=?",(duplicate,))
    main_dates=[('2025-09-02','18:30'),('2025-09-07','19:30'),('2025-09-09','18:30'),('2025-09-14','19:30'),('2025-09-16','18:30'),('2025-09-21','19:30'),('2025-09-23','18:30'),('2025-09-28','19:30')]
    visits=[{'date':d,'time':t,'group':'Север, сборная','type':'Абонемент','erp_subscription_id':'SUB-X','erp_attendance_id':'M'+str(i),'status':'присутствовал'} for i,(d,t) in enumerate(main_dates)]
    visits += [
      {'date':'2025-09-04','time':'19:45','group':'Юг, Доп. 6–8','type':'Отработка','erp_subscription_id':'OTHER1','erp_attendance_id':'X1','status':'присутствовал'},
      {'date':'2025-09-18','time':'19:45','group':'Юг, Доп. 6–8','type':'Отработка','erp_subscription_id':'OTHER2','erp_attendance_id':'X2','status':'присутствовал'},
      {'date':'2025-09-06','time':'16:00','group':'Юг, ОФП 4–5','type':'Отработка','erp_subscription_id':'OTHER3','erp_attendance_id':'X3','status':'присутствовал'},
      {'date':'2025-09-20','time':'17:00','group':'Юг, ОФП, журнал Доп. 18:45','type':'Отработка','erp_subscription_id':'OTHER4','erp_attendance_id':'X4','status':'присутствовал'}]
    payload={'child':'Тестовый Ребёнок','erp_student_id':'TEST-ERP','month':'2025-09','subscription':{'erp_id':'SUB-X','group':'Север, сборная','valid_from':'2025-09-02','valid_until':'2025-09-28','lesson_count':8},'attendance':visits}
    before=(con.execute('SELECT count(*) FROM payments').fetchone()[0],money.balance(con,sid),dict(con.execute('SELECT * FROM students WHERE id=?',(sid,)).fetchone()))
    plan=erphistory.preview(con,payload,'2025-09');assert plan['can_apply'] and plan['added']==13 and plan['review']==0,plan
    old=erphistory.PILOT_KEYS;erphistory.PILOT_KEYS={erphistory._pilot_key('TEST-ERP','2025-09')}
    try:r=erphistory.apply(con,payload,'2025-09',plan['token'],{'name':'Тест'})
    finally:erphistory.PILOT_KEYS=old
    assert (r['subscriptions_added'],r['visits_added'])==(1,12)
    again=erphistory.preview(con,payload,'2025-09');assert again['added']==0 and again['existing']==13 and again['review']==0
    events=clientattendance.calendar(con,sid,'2025-09')['events'];assert sum(x['kind']=='subscription' and x['status']=='present' for x in events)==8
    assert sum(x['kind']=='makeup' and x['status']=='present' for x in events)==4
    detail=services.student_detail(con,sid);hist=[x for x in detail['subscriptions'] if x.get('history_import')];assert len(hist)==1 and hist[0]['price'] is None
    after=(con.execute('SELECT count(*) FROM payments').fetchone()[0],money.balance(con,sid),dict(con.execute('SELECT * FROM students WHERE id=?',(sid,)).fetchone()))
    assert before==after
    con.execute("UPDATE students SET erp_id='' WHERE id=?",(sid,))
    con.execute("INSERT INTO erp_full_identities(namespace,source_id,student_id) VALUES ('student','TEST-ERP',?)",(sid,))
    linked=erphistory.preview(con,payload,'2025-09')
    assert linked['children'][0]['student']['id']==sid and linked['children'][0]['student']['match']=='saved_erp_link'
    print('ok')

if __name__=='__main__':main()
