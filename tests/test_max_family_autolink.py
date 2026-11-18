"""MAX identity linking requires explicit provider contact data, never names/message text."""
import tempfile
import unittest
from pathlib import Path
from crm import db,comms,services as sv


class MaxFamilyLinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.con=db.connect(str(Path(self.tmp.name)/'max.sqlite3'));self.addCleanup(self.con.close);db.init_db(self.con)
        self.user={'id':1,'role':'admin','name':'Synthetic'}
        self.data={'name':'Synthetic Child','parent_name':'Synthetic Parent','phone':'+79000000101'}

    def hook(self,phone=None):
        contact={'name':'Synthetic Parent'}
        if phone is not None:contact['phone']=phone
        comms.wz_hook(self.con,{'messages':[{'chatType':'max','chatId':'136000001','channelId':'synthetic-max-channel',
            'messageId':'synthetic-incoming-'+str(phone),'contact':contact,'text':'Synthetic contact +79000000101','type':'text'}]})
        return self.con.execute("SELECT * FROM conversations WHERE channel='wazzup_max' AND external_id='136000001'").fetchone()

    def create(self):
        sid=sv.create_new_lead(self.con,self.data,self.user)
        return sv.get_student_row(self.con,sid)['family_id']

    def test_provider_phone_before_card_links_existing_history(self):
        row=self.hook('+7 (900) 000-01-01')
        self.assertIsNone(row['family_id'])
        fid=self.create()
        result=comms.family_comms(self.con,fid,mark_read=False)
        self.assertEqual([c['id'] for c in result['conversations']],[row['id']])
        self.assertTrue(result['items'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM conversations').fetchone()[0],1)

    def test_no_phone_name_and_number_in_message_are_not_evidence(self):
        row=self.hook();fid=self.create()
        self.assertEqual(comms.family_comms(self.con,fid,mark_read=False)['conversations'],[])
        self.assertIsNone(self.con.execute('SELECT family_id FROM conversations WHERE id=?',(row['id'],)).fetchone()[0])

    def test_later_provider_phone_links_without_new_chat(self):
        row=self.hook();fid=self.create()
        updated=self.hook('+79000000101')
        self.assertEqual((updated['id'],updated['family_id']),(row['id'],fid))
        self.assertEqual(len(comms.family_comms(self.con,fid,mark_read=False)['items']),2)

    def test_manual_family_survives_relink_and_provider_updates(self):
        row=self.hook();fid=self.create();comms.link(self.con,row['id'],fid)
        other=self.con.execute("INSERT INTO families(name) VALUES ('Other synthetic')").lastrowid
        self.con.execute("INSERT INTO parents(family_id,name,phone,phone_norm) VALUES (?,'Other','+79000000102','79000000102')",(other,))
        updated=self.hook('+79000000102')
        comms.relink_family_conversations(self.con,other)
        self.assertEqual(updated['family_id'],fid)
        self.assertIn(row['id'],[c['id'] for c in comms.family_comms(self.con,fid,mark_read=False)['conversations']])

if __name__=='__main__':unittest.main()
