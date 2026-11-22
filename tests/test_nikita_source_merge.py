import tempfile
import unittest
from pathlib import Path

from crm import db, erppeople, erpsources, sources, services as sv


class NikitaSourceMergeTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(str(Path(tempfile.mkdtemp()) / "sources.sqlite3"))
        db.init_db(self.con)
        self.addCleanup(self.con.close)
        self.user = {"id": 1, "name": "Тест", "role": "admin"}

    def student(self, name, source, source_id=None):
        sid = sv.create_student(self.con, {"name": name, "parent_name": "Родитель", "phone": "+7900" + str(self.con.total_changes).zfill(7), "source": source}, self.user)
        self.con.execute("UPDATE students SET source_id=? WHERE id=?", (source_id, sid))
        return sid

    def test_existing_explicit_aliases_merge_and_other_promoters_stay(self):
        a = sources.ensure_source(self.con, "Промоутер", "Пром Никита 13.03.23")
        b = sources.ensure_source(self.con, "Промоутер", "Промоутер Никита Евсюнин 2025")
        # ensure_source itself canonicalizes future values; emulate old stored aliases.
        channel = self.con.execute("SELECT id FROM ad_channels WHERE name='Промоутер'").fetchone()[0]
        self.con.execute("INSERT INTO ad_sources(channel_id,name,created_at) VALUES (?,?,?)", (channel, "Пром Никита 24", "2026-01-01"))
        legacy = self.con.execute("SELECT id FROM ad_sources WHERE name='Пром Никита 24'").fetchone()[0]
        one = self.student("Первый", "Пром Никита 14.03.23")
        two = self.student("Второй", "старое", legacy)
        other_source = sources.ensure_source(self.con, "Промоутер", "Промоутер Иван Ивакин")
        other = self.student("Третий", "Промоутер Иван Ивакин", other_source)
        report = sources.merge_nikita_sources(self.con)
        canonical = sources.ensure_source(self.con, sources.NIKITA_CHANNEL, sources.NIKITA_SOURCE)
        self.assertEqual(report["changed"], 2)
        self.assertEqual([tuple(r) for r in self.con.execute("SELECT source,source_id FROM students WHERE id IN (?,?) ORDER BY id", (one, two))], [(sources.NIKITA_SOURCE, canonical)] * 2)
        self.assertEqual(self.con.execute("SELECT source_id FROM students WHERE id=?", (other,)).fetchone()[0], other_source)
        self.assertEqual(self.con.execute("SELECT active FROM ad_sources WHERE id=?", (legacy,)).fetchone()[0], 0)

    def test_future_erp_value_is_canonical_but_other_nikita_is_not(self):
        for alias in ("Пром Никита 13.03.23", "Пром Никита 14.03.23", "Пром Никита 24",
                      "Промо Никита 2026", "Промоутер Никита Евсюнин 2025", "Никита Евсюнин"):
            self.assertEqual(sources.canonical_source("Другое", alias), (sources.NIKITA_CHANNEL, sources.NIKITA_SOURCE))
        self.assertEqual(sources.canonical_source("Другое", "Промоутер Никита Иванов"), ("Другое", "Промоутер Никита Иванов"))
        preview = erppeople._profile_preview(self.con, None, {"source": "Пром Никита 14.03.23"})
        self.assertEqual(preview["source"]["source"], sources.NIKITA_SOURCE)

    def test_erp_reconciliation_treats_alias_rows_as_one_source(self):
        sid = self.student("Клиент ERP", "")
        self.con.execute("UPDATE students SET erp_id='64928' WHERE id=?", (sid,))
        text = "Тип;ID;Канал;Источник\nstudent;64928;Промоутер;Пром Никита 13.03.23\nstudent;64928;Другое;Промоутер Никита Евсюнин 2025"
        report = erpsources.plan(self.con, text)
        self.assertEqual(report["issues"], [])
        self.assertEqual([(x["channel"], x["name"]) for x in report["changes"]], [(sources.NIKITA_CHANNEL, sources.NIKITA_SOURCE)])


if __name__ == "__main__":
    unittest.main()
