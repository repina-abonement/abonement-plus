import tempfile
import unittest
from pathlib import Path
from crm import db, sources, services, analytics

class OtherSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.con=db.connect(str(Path(self.tmp.name)/'test.sqlite'));db.init_db(self.con)
        self.user={'id':1,'role':'operator','name':'Тест'}
        self.source=self.con.execute("SELECT id FROM ad_sources WHERE name='Прочие'").fetchone()[0]
    def create(self, comment):
        return services.create_student(self.con,{'name':'Тест','parent_name':'Тест','phone':'+79000000'+str(100+self.con.execute('SELECT COUNT(*) FROM students').fetchone()[0]),
            'source_id':self.source,'source_comment':comment},self.user)
    def test_validation_before_creation_and_update(self):
        families=self.con.execute('SELECT COUNT(*) FROM families').fetchone()[0]
        for value in ('', '   ', None, 'x'*1001):
            with self.assertRaises(services.ApiError):self.create(value)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM families').fetchone()[0],families)
        sid=self.create(' Чат ЖК ')
        self.assertEqual(services.get_student_row(self.con,sid)['source_comment'],'Чат ЖК')
        with self.assertRaises(services.ApiError):
            services.update_student(self.con,sid,{'name':'Не сохранить','source_comment':' '},self.user)
        self.assertEqual(services.get_student_row(self.con,sid)['name'],'Тест')
        services.update_student(self.con,sid,{'source_comment':'Объявление у дома'},self.user)
        self.assertEqual(services.get_student_row(self.con,sid)['source_comment'],'Объявление у дома')
        services.update_student(self.con,sid,{'source_id':''},self.user)
        self.assertEqual(services.get_student_row(self.con,sid)['source_comment'],'')
        with self.assertRaises(services.ApiError):
            services.update_student(self.con,sid,{'source_id':self.source},self.user)
    def test_analytics_and_idempotent_seed(self):
        sources.init(self.con)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ad_sources WHERE name='Прочие'").fetchone()[0],1)
        self.create('Чат ЖК');self.create('Чат ЖК');self.create('Листовка в кафе')
        self.con.execute("UPDATE students SET created_at='2026-11-27T12:00'")
        rep=sources.report(self.con,'2026-11-01','2026-11-30')
        row=next(x for x in rep['sources'] if x['source']=='Прочие')
        self.assertEqual(row['leads'],3)
        self.assertEqual(row['comments'],{'Чат ЖК':2,'Листовка в кафе':1})
        rep=analytics.sources_report(self.con,'2026-11')
        self.assertEqual(rep['total']['leads'],3)
        self.assertEqual(rep['sources'][0]['comments'],row['comments'])

if __name__=='__main__':unittest.main()
