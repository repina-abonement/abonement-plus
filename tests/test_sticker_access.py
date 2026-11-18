import sqlite3
import unittest
from crm import stickerpacks, comms


class StickerAccess(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        self.con.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,active INTEGER)')
        self.con.execute("INSERT INTO users VALUES(7,'Ольга Авлахова',1)")
        stickerpacks.init(self.con)
        self.con.execute("INSERT INTO sticker_packs VALUES('Footbolika_Olya','Футболика • Оля на связи')")
        self.con.execute("INSERT INTO custom_stickers VALUES('secret','Footbolika_Olya','file','',0,X'00','image/png')")

    def test_catalog_and_send_reject_other_accounts_before_transport(self):
        for user in (None, {'id':8,'role':'admin'}, {'id':8,'name':'Ольга Авлахова'}):
            self.assertEqual(stickerpacks.catalog(self.con,user)['packs'], [])
            with self.assertRaises(comms.CommsError):
                comms.send_sticker(self.con,1,'custom:secret',user or {})
        self.assertEqual(len(stickerpacks.catalog(self.con,{'id':7})['packs']),1)

    def test_ambiguous_or_inactive_owner_fails_closed(self):
        self.con.execute("INSERT INTO users VALUES(9,'Ольга Авлахова',1)")
        self.assertEqual(stickerpacks.catalog(self.con,{'id':7})['packs'],[])
        self.con.execute('UPDATE users SET active=0')
        self.assertEqual(stickerpacks.catalog(self.con,{'id':7})['packs'],[])
