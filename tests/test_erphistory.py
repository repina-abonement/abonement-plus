"""Обезличенный исторический перенос ERP без финансовых эффектов."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import analytics, db, erphistory, schedule, services, clientattendance, money


def main():
    con=db.connect(os.path.join(tempfile.mkdtemp(),'crm.sqlite3'));db.init_db(con)
    ar=con.execute("INSERT INTO branches(name) VALUES ('Север')").lastrowid
    nb=con.execute("INSERT INTO branches(name) VALUES ('Юг')").lastrowid
    main_group=schedule.save_group(con,{'name':'Сборная Север','branch_id':ar,'weekdays':[1,6],'time':'18:30','day_times':{'6':'19:30'}})
    makeup_groups=[]
    for name,weekdays,tm in [('Доп. 6–8',[3],'19:45'),('ОФП 4–5',[5],'16:00'),('ОФП 6–8',[5],'17:00')]:
        makeup_groups.append(schedule.save_group(con,{'name':name,'branch_id':nb,'weekdays':weekdays,'time':tm}))
    schedule.save_group(con,{'name':'Доп. 9–11','branch_id':nb,'weekdays':[3],'time':'19:45'})
    schedule.save_group(con,{'name':'Доп. 6–8 другой день','branch_id':nb,'weekdays':[0],'time':'19:45'})
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
    payload={'child':'Тестовый Ребёнок','erp_student_id':'TEST-ERP','month':'2025-09','subscription':{'erp_id':'SUB-X','group':'Север, сборная','valid_from':'2025-09-02','valid_until':'2025-09-28','lesson_count':8,'price':12345},'attendance':visits}
    before=(con.execute('SELECT count(*) FROM payments').fetchone()[0],money.balance(con,sid),dict(con.execute('SELECT * FROM students WHERE id=?',(sid,)).fetchone()))
    plan=erphistory.preview(con,payload,'2025-09');assert plan['can_apply'] and plan['added']==13 and plan['review']==0,plan
    old=erphistory.PILOT_KEYS;erphistory.PILOT_KEYS={erphistory._pilot_key('TEST-ERP','2025-09')}
    try:r=erphistory.apply(con,payload,'2025-09',plan['token'],{'name':'Тест'})
    finally:erphistory.PILOT_KEYS=old
    assert (r['subscriptions_added'],r['visits_added'])==(1,12)
    again=erphistory.preview(con,payload,'2025-09');assert again['added']==0 and again['existing']==13 and again['review']==0
    events=clientattendance.calendar(con,sid,'2025-09')['events'];assert sum(x['kind']=='subscription' and x['status']=='present' for x in events)==8
    assert sum(x['kind']=='makeup' and x['status']=='present' for x in events)==4
    detail=services.student_detail(con,sid);hist=[x for x in detail['subscriptions'] if x.get('history_import')];assert len(hist)==1 and hist[0]['price']==12345 and hist[0]['analytics_only']
    assert next(x for x in analytics.groups_report(con,'2025-09')['groups'] if x['id']==main_group)['revenue']==12345
    dashboard=services.dashboard(con,'2025-09')
    assert dashboard['kpi']['revenue']['value']==12345 and dashboard['kpi']['holders']['value']==1
    assert dashboard['kpi']['fill']['subs_count']==1 and dashboard['kpi']['avg']['value']==12345
    after=(con.execute('SELECT count(*) FROM payments').fetchone()[0],money.balance(con,sid),dict(con.execute('SELECT * FROM students WHERE id=?',(sid,)).fetchone()))
    assert before==after
    con.execute("UPDATE students SET erp_id='' WHERE id=?",(sid,))
    con.execute("INSERT INTO erp_full_identities(namespace,source_id,student_id) VALUES ('student','TEST-ERP',?)",(sid,))
    linked=erphistory.preview(con,payload,'2025-09')
    assert linked['children'][0]['student']['id']==sid and linked['children'][0]['student']['match']=='saved_erp_link'
    con.execute("DELETE FROM erp_full_identities WHERE namespace='student' AND source_id='TEST-ERP'")
    manual=erphistory.preview(con,payload,'2025-09',{'TEST-ERP':sid})
    assert manual['can_apply'] and manual['children'][0]['student']['match']=='manual_selection',manual
    other=con.execute("INSERT INTO students(family_id,name,birth,stage_changed_at,created_at) VALUES (?,?,?,?,?)",
                      (fid,'Другой Ребёнок','2015-01-01','2026-01-01','2026-01-01')).lastrowid
    wrong=erphistory.preview(con,payload,'2025-09',{'TEST-ERP':other})
    assert not wrong['can_apply'] and 'ФИО' in wrong['children'][0]['issues'][0]
    old=erphistory.PILOT_KEYS;erphistory.PILOT_KEYS={erphistory._pilot_key('TEST-ERP','2025-09')}
    try: erphistory.apply(con,payload,'2025-09',manual['token'],{'name':'Тест'},{'TEST-ERP':sid})
    finally: erphistory.PILOT_KEYS=old
    automatic=erphistory.preview(con,payload,'2025-09')
    assert automatic['can_apply'] and automatic['children'][0]['student']['match']=='saved_erp_link'
    without_price={**payload,'subscription':{**{k:v for k,v in payload['subscription'].items() if k!='price'},'erp_id':'SUB-NO-PRICE'},'erp_student_id':'OTHER-ERP'}
    missing=erphistory.preview(con,without_price,'2025-09',{'OTHER-ERP':sid})
    assert not missing['can_apply'] and missing['children'][0]['subscription']['reason']=='В источнике нет точной стоимости абонемента'
    schedule.save_group(con,{'name':'ОФП без возраста','branch_id':nb,'weekdays':[5],'time':'17:00'})
    ambiguous={**payload,'attendance':payload['attendance']+[{
      'date':'2025-09-27','time':'17:00','group':'Юг, ОФП, журнал без возраста',
      'type':'Отработка','erp_subscription_id':'OTHER5','erp_attendance_id':'X5','status':'присутствовал'}]}
    unresolved=erphistory.preview(con,ambiguous,'2025-09')
    mark=next(x for x in unresolved['children'][0]['visits'] if x['source_id']=='X5')
    assert not unresolved['can_apply'] and mark['state']=='review' and mark['approvable_unassigned']
    approved=erphistory.preview(con,ambiguous,'2025-09',unassigned_visit_ids=['X5'])
    mark=next(x for x in approved['children'][0]['visits'] if x['source_id']=='X5')
    assert approved['can_apply'] and mark['state']=='add_unassigned' and mark['group_unresolved']
    old=erphistory.PILOT_KEYS;erphistory.PILOT_KEYS={erphistory._pilot_key('TEST-ERP','2025-09')}
    try: imported=erphistory.apply(con,ambiguous,'2025-09',approved['token'],{'name':'Тест'},unassigned_visit_ids=['X5'])
    finally: erphistory.PILOT_KEYS=old
    assert imported['visits_added']==1
    saved=con.execute("SELECT * FROM erp_history_unassigned_visits WHERE source_id='X5'").fetchone()
    assert saved and saved['student_id']==sid and saved['source_group']=='Юг, ОФП, журнал без возраста'
    event=next(x for x in clientattendance.calendar(con,sid,'2025-09')['events'] if x.get('erp_attendance_id')=='X5')
    assert event['group_id'] is None and event['group']=='Группа не определена' and event['group_unresolved'] and event['time']=='17:00'
    repeated=erphistory.preview(con,ambiguous,'2025-09',unassigned_visit_ids=['X5'])
    assert repeated['added']==0 and repeated['review']==0 and repeated['existing']==14
    resolved=erphistory.preview(con,ambiguous,'2025-09',visit_group_ids={'X5':makeup_groups[2]})
    resolved_mark=next(x for x in resolved['children'][0]['visits'] if x['source_id']=='X5')
    assert resolved['can_apply'] and resolved['updated']==1 and resolved['existing']==13 and resolved_mark['state']=='assign_group'
    old=erphistory.PILOT_KEYS;erphistory.PILOT_KEYS={erphistory._pilot_key('TEST-ERP','2025-09')}
    try: corrected=erphistory.apply(con,ambiguous,'2025-09',resolved['token'],{'name':'Тест'},visit_group_ids={'X5':makeup_groups[2]})
    finally: erphistory.PILOT_KEYS=old
    assert corrected['visits_added']==0 and corrected['visits_updated']==1
    assert con.execute("SELECT 1 FROM erp_history_unassigned_visits WHERE source_id='X5'").fetchone() is None
    saved_group=con.execute("SELECT group_id FROM erp_history_visits WHERE source_id='X5'").fetchone()
    assert saved_group and saved_group['group_id']==makeup_groups[2]
    event=next(x for x in clientattendance.calendar(con,sid,'2025-09')['events'] if x.get('erp_attendance_id')=='X5')
    assert event['group_id']==makeup_groups[2] and 'ОФП' in event['group'] and not event.get('group_unresolved')
    final=erphistory.preview(con,ambiguous,'2025-09')
    assert final['added']==0 and final['updated']==0 and final['review']==0 and final['existing']==14

    # Second pilot shape: two subscriptions, explicit absences, makeups, and
    # two distinct timed marks on the same day. The original singular format
    # above remains covered unchanged.
    second_group=schedule.save_group(con,{'name':'Вторая сборная','branch_id':ar,'weekdays':[3],'time':'20:00'})
    con.execute("INSERT INTO enrollments(student_id,group_id,created_at) VALUES (?,?,?)",(sid,second_group,'2025-01-01'))
    first_marks=[]
    for i,(d,t,status) in enumerate([
      ('2025-10-02','20:00','присутствовал'),('2025-10-09','20:00','присутствовал'),
      ('2025-10-16','20:00','присутствовал'),('2025-10-23','20:00','отсутствовал'),
      ('2025-10-30','20:00','отсутствовал')]):
        first_marks.append({'date':d,'time':t,'group':'Север, Вторая сборная','type':'Абонемент','erp_subscription_id':'SUB-A','erp_attendance_id':f'A{i}','status':status})
    second_marks=[]
    for i,(d,t,status) in enumerate([
      ('2025-10-05','19:30','присутствовал'),('2025-10-07','18:30','присутствовал'),
      ('2025-10-12','19:30','присутствовал'),('2025-10-14','18:30','присутствовал'),
      ('2025-10-19','19:30','присутствовал'),('2025-10-21','18:30','отсутствовал'),
      ('2025-10-26','19:30','отсутствовал'),('2025-10-28','18:30','отсутствовал')]):
        second_marks.append({'date':d,'time':t,'group':'Север, сборная','type':'Абонемент','erp_subscription_id':'SUB-B','erp_attendance_id':f'B{i}','status':status})
    # A prior child's confirmed group for this exact lesson is reusable.
    prior_student=con.execute("INSERT INTO students(family_id,name,birth,stage_changed_at,created_at) VALUES (?,?,?,?,?)",
      (fid,'Предыдущий пилот','2014-01-01','2026-01-01','2026-01-01')).lastrowid
    con.execute("""INSERT INTO erp_history_visits(source_id,source_subscription_id,student_id,group_id,visit_date,visit_time,kind,status,absence_source_id,source_payload,imported_at,imported_by)
      VALUES ('PRIOR-MU',NULL,?,?,?,'17:00','makeup','present',NULL,?,'2026-01-01','Тест')""",
      (prior_student,makeup_groups[2],'2025-10-18',erphistory._json({'group':'Юг, ОФП, журнал без возраста'})))
    makeups=[
      {'date':'2025-10-04','time':'16:00','group':'Юг, ОФП 4–5','type':'Отработка','erp_subscription_id':'OLD','erp_attendance_id':'MU1','status':'присутствовал'},
      {'date':'2025-10-04','time':'17:00','group':'Юг, ОФП 6–8','type':'Отработка','erp_subscription_id':'OLD','erp_attendance_id':'MU2','status':'присутствовал'},
      {'date':'2025-10-18','time':'17:00','group':'Юг, ОФП, журнал без возраста','type':'Отработка','erp_subscription_id':'OLD','erp_attendance_id':'MU3','status':'присутствовал'}]
    multi={'child':'Тестовый Ребёнок','erp_student_id':'TEST-ERP','month':'2025-10','subscriptions':[
      {'erp_id':'SUB-A','valid_from':'2025-10-02','valid_until':'2025-10-30','lesson_count':5,'price':7000},
      {'erp_id':'SUB-B','valid_from':'2025-10-05','valid_until':'2025-10-28','lesson_count':8,'price':9000}],
      'attendance':first_marks+second_marks+makeups}
    multi_plan=erphistory.preview(con,multi,'2025-10')
    assert multi_plan['can_apply'] and multi_plan['added']==18 and multi_plan['review']==0,multi_plan
    old=erphistory.PILOT_KEYS;erphistory.PILOT_KEYS={erphistory._pilot_key('TEST-ERP','2025-10')}
    try: multi_result=erphistory.apply(con,multi,'2025-10',multi_plan['token'],{'name':'Тест'})
    finally: erphistory.PILOT_KEYS=old
    assert (multi_result['subscriptions_added'],multi_result['visits_added'])==(2,16)
    multi_events=clientattendance.calendar(con,sid,'2025-10')['events']
    assert sum(x['status']=='present' for x in multi_events)==11
    assert sum(x['kind']=='makeup' and x['status']=='present' for x in multi_events)==3
    assert sum(x['status']=='absent' for x in multi_events)==5
    same_day=[x for x in multi_events if x['date']=='2025-10-04']
    assert {x['time'] for x in same_day}=={'16:00','17:00'}
    multi_again=erphistory.preview(con,multi,'2025-10')
    assert multi_again['added']==0 and multi_again['existing']==18 and multi_again['review']==0
    print('ok')

if __name__=='__main__':main()
