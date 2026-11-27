import unittest

from crm.group_seats import seats


class GroupSeatsTest(unittest.TestCase):
    def test_branch_aliases_and_fallback(self):
        for branch, expected in [
            ('ТЦ «Небо»', 10), ('ТРЦ Небо', 10),
            ('Чоботовская 4с2', 12), ('СК «Западный»', 12),
            ('СК «Западный» (Чоботовская)', 12), ('Арктика', 14), ('СК «Арктика»', 14), ('Пыхтино РКШ', 9),
        ]:
            with self.subTest(branch=branch):
                self.assertEqual(seats(branch, {}, 9), expected)

    def test_morning_group_uses_eight_seats(self):
        for group in [
            {'name': 'Небо · Пн/Ср 10:00 · 1,5–3'},
            {'source_name': 'Малыши Пн/Ср 10:30'},
            {'age': '1.5-3', 'weekdays': [0, 2]},
            {'weekdays': '0,2', 'time': '10:00'},
            {'weekdays': '0,2', 'time': '10:30'},
        ]:
            with self.subTest(group=group):
                self.assertEqual(seats('ТЦ «Небо»', group, 12), 8)

    def test_toddlers_exception_does_not_change_other_groups(self):
        for group in [
            {'name': 'Небо · Сб/Вс 10:00 · 1,5–3'},
            {'name': 'СТОП Небо ПУП пн/чт/сб 17:45 возраст 4-5'},
            {'age': '3–4', 'weekdays': '0,2', 'time': '10:30'},
            {'age': '1,5–3', 'weekdays': '0,2', 'is_makeup': 1},
        ]:
            with self.subTest(group=group):
                self.assertEqual(seats('ТЦ Небо', group, 12), 10)
