import tempfile,unittest
from pathlib import Path
from crm import db,sources,services as sv

class SourcesTest(unittest.TestCase):
    def test_edit_move_archive_preserves_references_and_restart(self):
        c=db.connect(str(Path(tempfile.mkdtemp())/'test.sqlite3'));db.init_db(c)
        channel=c.execute("SELECT id FROM ad_channels WHERE name='Вконтакте'").fetchone()[0]
        sid=sources.save_source(c,{'channel_id':channel,'name':'Первый'})
        student=sv.create_student(c,{'name':'Ребёнок','parent_name':'Анна','phone':'+79002223344','source_id':sid},{'id':1,'name':'Админ'})
        target=sources.save_channel(c,{'name':'Новый канал'})
        sources.save_channel(c,{'name':'ВК'},channel)
        sources.save_source(c,{'name':'Переименован','channel_id':target},sid)
        self.assertEqual(sources.label(c,sid),'Новый канал: Переименован')
        self.assertEqual(c.execute('SELECT source_id FROM students WHERE id=?',(student,)).fetchone()[0],sid)
        sources.save_channel(c,{'active':False},target)
        self.assertFalse(any(x['id']==sid for x in sources.listing(c)['sources']))
        self.assertTrue(any(x['id']==sid for x in sources.listing(c,False)['sources']))
        sources.init(c)
        self.assertIsNone(c.execute("SELECT id FROM ad_channels WHERE name='Вконтакте'").fetchone())
        with self.assertRaises(sv.ApiError):sources.save_channel(c,{'name':'вк'})
        with self.assertRaises(sv.ApiError):sources.save_source(c,{'name':'Другой','channel_id':target})
        with self.assertRaises(sv.ApiError):sources.save_channel(c,{'name':'Новое','expected':{'name':'Старое','active':1}},channel)

if __name__=='__main__':unittest.main()
