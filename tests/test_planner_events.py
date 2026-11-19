import unittest
from crm import planner,plannerevents
from tests.test_planner_shifts import PlannerShifts

class SharedEvents(unittest.TestCase):
    setUp=PlannerShifts.setUp
    def test_shared_time_and_history_without_client_changes(self):
        event=plannerevents.save(self.con,self.offline,{'title':'Synthetic birthday','day':'2026-11-28','start_time':'18:00','end_time':'19:00','participants':'Synthetic participants'})
        owner=dict(self.con.execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone())
        for actor in [self.offline,self.varya,owner]:
            day=planner.week(self.con,actor,'2026-11-28',self.pool,rolling=True,days=1)['days'][0]
            self.assertEqual(day['events'][0]['id'],event['id']);self.assertEqual(day['events'][0]['start_time'],'18:00')
        own_day=planner.week(self.con,owner,'2026-11-28',owner['id'],rolling=True,days=1)['days'][0]
        self.assertEqual(own_day['events'][0]['id'],event['id'])
        self.assertEqual(own_day['events'][0]['end_time'],'19:00')
        with self.assertRaises(planner.PlanError):plannerevents.save(self.con,{'id':90,'role':'trainee'},{'title':'Denied'})
        with self.assertRaises(planner.PlanError):plannerevents.save(self.con,self.offline,{'title':'Invalid','day':'2026-11-28','start_time':'19:00','end_time':'18:00'})
        plannerevents.save(self.con,self.varya,{'title':'Synthetic corrected','day':'2026-11-28','start_time':'18:00','end_time':'19:00'},event['id'])
        self.assertEqual(self.con.execute('SELECT participants FROM planner_events WHERE id=?',(event['id'],)).fetchone()[0],'Synthetic participants')
        plannerevents.save(self.con,self.varya,{'cancelled':True},event['id'])
        self.assertEqual(plannerevents.listing(self.con,self.offline,'2026-11-28'),[])
        self.assertEqual(self.con.execute('SELECT count(*) FROM planner_event_history').fetchone()[0],3)
        self.assertEqual(self.con.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
