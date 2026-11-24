import tempfile
import unittest
from pathlib import Path

from crm import db, services, targetedtrainerresidual as residual


class TrainerResidualTest(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(str(Path(tempfile.mkdtemp())/'trainer-residual.sqlite3'))
        db.init_db(self.con)
        self.user={'id':1,'role':'admin','name':'Руководитель'}

    def add(self,name,phone):
        return services.create_student(self.con,{'name':name,'parent_name':'Родитель','phone':phone},self.user)

    def test_rows_are_stable_and_source_fields_are_complete(self):
        self.assertEqual(84,len(residual.ROWS))
        self.assertTrue(residual.KEY.endswith('_v2'))
        self.assertEqual(len(residual.ROWS),len(set(residual.ROWS)))
        self.assertTrue(all(message and day and tm and name for message,day,tm,name in residual.ROWS))

    def test_full_name_nickname_and_verified_typo_match(self):
        daniil=self.add('Даниил Бирюков','+79000000001')
        mamatakhunov=self.add('Маматахунов Фирдавс','+79000000002')
        magnin=self.add('Максим Магнин','+79000000003')
        self.assertEqual([daniil],[r['id'] for r in residual._matches(self.con,'Даня Бирюков')])
        self.assertEqual([mamatakhunov],[r['id'] for r in residual._matches(self.con,'Фердавс Маматахунов')])
        self.assertEqual([magnin],[r['id'] for r in residual._matches(self.con,'Магин')])

    def test_first_name_is_rejected_and_namesakes_stay_ambiguous(self):
        self.add('Замир Кадымов','+79000000004')
        self.assertEqual([],residual._matches(self.con,'Замир'))
        first=self.add('Мария Шарикова','+79000000005')
        second=self.add('Евгения Шарикова','+79000000006')
        self.assertEqual({first,second},{r['id'] for r in residual._matches(self.con,'Шарикова')})


if __name__=='__main__':unittest.main()
