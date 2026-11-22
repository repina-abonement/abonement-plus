"""Точная матрица исходов клиентских задач ERP без догадок о статусах."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from crm import comms, db, services, tasks


class ErpWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "erp-workflow.sqlite3"))
        db.init_db(self.con)
        self.sid = services.create_student(
            self.con,
            {"name": "Тестовый Ребёнок", "parent_name": "Родитель", "phone": "+7 900 000-00-01"},
            {"name": "Тест"},
        )
        self.user = {"name": "Ольга", "role": "operator"}

    def add(self, title, kind="manual", source="erp_rule"):
        return tasks.add_task(
            self.con, kind, self.sid, "2026-11-21T12:00", title=title,
            source=source, auto_key=f"test:{title}:{kind}",
        )

    def test_exact_outcome_creates_one_next_task_and_does_not_change_stage(self):
        tid = self.add("Новый лид", "call_lead")
        before = self.con.execute("SELECT stage FROM students WHERE id=?", (self.sid,)).fetchone()[0]
        shown = tasks.for_student(self.con, self.sid)["open"][0]
        self.assertEqual(shown["erp_outcomes"], [
            "Новый лид (ндз)", "Новый лид (думают)",
            "Напоминание о ПУ (звонок)", "Закрыть задачу",
        ])
        tasks.complete(self.con, tid, self.user, "Новый лид (ндз)", "Не дозвонились, повторить звонок")
        nxt = self.con.execute(
            "SELECT kind,title,source FROM tasks WHERE student_id=? AND status='open'", (self.sid,)
        ).fetchall()
        self.assertEqual([tuple(x) for x in nxt], [("no_answer", "Новый лид (ндз)", "erp_rule")])
        self.assertEqual(self.con.execute("SELECT stage FROM students WHERE id=?", (self.sid,)).fetchone()[0], before)
        with self.assertRaises(comms.CommsError):
            tasks.complete(self.con, tid, self.user, "Новый лид (ндз)", "Повтор")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND status='open'", (self.sid,)).fetchone()[0], 1)

    def test_invalid_outcome_leaves_task_open(self):
        tid = self.add("Почему не купили", "why_not_bought")
        with self.assertRaises(comms.CommsError):
            tasks.complete(self.con, tid, self.user, "Любой комментарий")
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0], "open")

    def test_two_how_was_templates_stay_distinct(self):
        sms = self.add("Напоминание о ПУ (смс)", "trial_confirm")
        tasks.complete(self.con, sms, self.user, "Как прошло ПУ?", "Пробное завершилось, уточнить впечатления")
        sms_task = self.con.execute("SELECT * FROM tasks WHERE kind='erp_how_was_sms'").fetchone()
        self.assertEqual(tasks._task_out(self.con, sms_task)["erp_outcomes"], [
            "Проверить продление", "Почему не купили", "Нет отметки о ПУ", "Не пришли на ПУ",
        ])

        today = self.add("Напоминание о сегодняшнем ПУ (звонок и смс)", "trial_today")
        tasks.complete(self.con, today, self.user, "Как прошло ПУ?", "Пробное завершилось, уточнить впечатления")
        today_task = self.con.execute("SELECT * FROM tasks WHERE kind='erp_how_was_today'").fetchone()
        self.assertEqual(tasks._task_out(self.con, today_task)["erp_outcomes"], [
            "Нет отметки о ПУ", "Не пришли на ПУ", "Проверить продление", "Почему не купили",
        ])

    def test_manual_same_title_is_not_hijacked(self):
        tid = self.add("Новый лид", "manual", "manual")
        shown = tasks._task_out(self.con, self.con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())
        self.assertEqual(shown["erp_outcomes"], [])
        tasks.complete(self.con, tid, self.user, "обычная заметка")

    def test_trial_call_uses_trial_time_minus_23_59(self):
        self.con.execute("UPDATE students SET trial_at='2026-11-25T18:00' WHERE id=?", (self.sid,))
        tid = self.add("Новый лид", "call_lead")
        tasks.complete(self.con, tid, self.user, "Напоминание о ПУ (звонок)", "Записались на пробное")
        due = self.con.execute("SELECT due_at FROM tasks WHERE student_id=? AND status='open'", (self.sid,)).fetchone()[0]
        self.assertEqual(due, "2026-11-24T18:01")

    def test_erp_transition_requires_and_preserves_comment(self):
        tid = self.add("Новый лид", "call_lead")
        with self.assertRaisesRegex(comms.CommsError, "комментарий"):
            tasks.complete(self.con, tid, self.user, "Новый лид (думают)")
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0], "open")
        tasks.complete(self.con, tid, self.user, "Новый лид (думают)", "Мама обсудит расписание и ответит завтра")
        current = self.con.execute("SELECT result FROM tasks WHERE id=?", (tid,)).fetchone()[0]
        following = self.con.execute("SELECT title,note FROM tasks WHERE student_id=? AND status='open'", (self.sid,)).fetchone()
        history = self.con.execute("SELECT text FROM history WHERE student_id=? ORDER BY id DESC", (self.sid,)).fetchone()[0]
        self.assertIn("Мама обсудит расписание", current)
        self.assertEqual(tuple(following), ("Новый лид (думают)", "Мама обсудит расписание и ответит завтра"))
        self.assertIn("Комментарий: Мама обсудит расписание", history)

    def test_selected_next_task_date_is_applied_in_same_transition(self):
        tid = self.add("Новый лид", "call_lead")
        tasks.complete(self.con, tid, self.user, "Новый лид (ндз)", "Перезвонить завтра", "2026-11-23")
        following = self.con.execute("SELECT title,due_at FROM tasks WHERE student_id=? AND status='open'", (self.sid,)).fetchone()
        self.assertEqual(tuple(following), ("Новый лид (ндз)", "2026-11-23T23:59"))
        history = self.con.execute("SELECT text FROM history WHERE student_id=? ORDER BY id DESC", (self.sid,)).fetchone()[0]
        self.assertIn("Срок следующей задачи: 23.11.2026", history)

    def test_explicit_empty_next_date_is_rejected_before_closing(self):
        tid = self.add("Новый лид", "call_lead")
        with self.assertRaisesRegex(comms.CommsError, "дату следующей задачи"):
            tasks.complete(self.con, tid, self.user, "Новый лид (ндз)", "Перезвонить", "")
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0], "open")


if __name__ == "__main__":
    unittest.main()
