import unittest
import tempfile
from pathlib import Path

from crm import db, services as sv, tasks


class TaskDeleteTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(str(Path(tempfile.mkdtemp()) / "task-delete.sqlite3"))
        db.init_db(self.con)
        self.admin = dict(self.con.execute("SELECT id,name,role FROM users WHERE role='admin' LIMIT 1").fetchone())
        self.student = sv.create_student(self.con, {"name": "Тестовый Ребёнок", "parent_name": "Родитель", "phone": "+79000000001"}, self.admin)

    def test_erp_task_is_cancelled_not_completed_and_not_recreated(self):
        tid = tasks.import_erp_task(self.con, "Почему не купили", self.student, "2026-11-24T23:59", "ERP")
        result = tasks.remove(self.con, tid, self.admin)
        row = self.con.execute("SELECT status,result,auto_key FROM tasks WHERE id=?", (tid,)).fetchone()

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual((row["status"], row["result"]), ("cancelled", "Удалена администратором"))
        self.assertIsNone(tasks.import_erp_task(self.con, "Почему не купили", self.student, "2026-11-24T23:59", "ERP"))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE auto_key=?", (row["auto_key"],)).fetchone()[0], 1)
        self.assertTrue(self.con.execute("SELECT 1 FROM history WHERE student_id=? AND text LIKE '%удалена%'", (self.student,)).fetchone())

    def test_only_open_tasks_can_be_deleted(self):
        tid = tasks.add_task(self.con, "manual", self.student, "2026-11-24T23:59", title="Проверить", source="manual")
        tasks.remove(self.con, tid, self.admin)
        with self.assertRaisesRegex(Exception, "только открытую"):
            tasks.remove(self.con, tid, self.admin)

    def test_delete_controls_use_the_delete_endpoint(self):
        root = Path(__file__).parents[1] / "crm/static"
        task_ui = (root / "tasks.js").read_text()
        planner_ui = (root / "planner.js").read_text()
        self.assertIn("data-task-delete", task_ui)
        self.assertIn("api('DELETE',`/api/tasks/${t.id}`)", task_ui)
        self.assertIn("data-delete-planner-task", planner_ui)
        self.assertIn("api('DELETE',`/api/tasks/${id}`)", planner_ui)


if __name__ == "__main__":
    unittest.main()
