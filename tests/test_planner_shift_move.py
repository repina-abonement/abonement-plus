import unittest
from datetime import date
from crm import planner
from tests.test_planner_shifts import PlannerShifts

class ShiftMove(unittest.TestCase):
    setUp=PlannerShifts.setUp
    def test_one_occurrence_moves_without_duplicates_or_checklist_loss(self):
        rid=planner.add(self.con,self.varya,{'title':'Synthetic','date':'2026-11-23','assignee_id':self.pool,'repeat':{'rule':'daily'},'subtasks':['Cash']})
        planner._materialize(self.con,date(2026,11,23))
        task=self.con.execute('SELECT * FROM tasks WHERE recur_id=?',(rid,)).fetchone()
        item=planner.task_detail(self.con,task['id'],self.varya)['task']['subtasks'][0]
        planner.change_subtask(self.con,task['id'],self.offline,{'done':True},item['id'])
        for shift in ['morning','evening','common']:
            planner.move(self.con,task['id'],self.varya,'2026-11-23',shift)
            planner._materialize(self.con,date(2026,11,23))
            self.assertEqual(self.con.execute('SELECT count(*) FROM tasks WHERE recur_id=?',(rid,)).fetchone()[0],1)
            current=planner.task_detail(self.con,task['id'],self.varya)['task']
            self.assertEqual(current['shift'],shift);self.assertEqual(current['subtasks'][0]['done'],1)
        planner._materialize(self.con,date(2026,11,25))
        self.assertEqual(self.con.execute("SELECT shift FROM tasks WHERE substr(due_at,1,10)='2026-11-25'").fetchone()[0],'common')
        with self.assertRaises(planner.PlanError):planner.move(self.con,task['id'],self.other,'2026-11-23','evening')
        with self.assertRaises(planner.PlanError):planner.move(self.con,task['id'],self.varya,'2026-11-24','morning')
        with self.assertRaises(planner.PlanError):planner.move(self.con,task['id'],self.varya,'2026-11-23','both')
