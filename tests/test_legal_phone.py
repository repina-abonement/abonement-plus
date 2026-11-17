import tempfile,unittest
from pathlib import Path
from crm import db,legal,comms

class LegalPhoneTest(unittest.TestCase):
 def test_owner_contact_is_scoped_and_does_not_overwrite_later_edits(self):
  con=db.connect(str(Path(tempfile.mkdtemp())/'legal.sqlite3'));self.addCleanup(con.close);db.init_db(con)
  comms.put(con,'legal_inn','770000000000');legal.provision_school_phone(con)
  self.assertEqual(comms.get(con,'legal_phone'),'')
  comms.put(con,'legal_inn','183210983596');legal.provision_school_phone(con)
  self.assertEqual(comms.get(con,'legal_phone'),'+7 991 615-09-60')
  self.assertIn('+7 991 615-09-60',legal.page(con,'privacy','https://crm.example.ru'))
  comms.put(con,'legal_phone','+7 900 000-00-00');legal.provision_school_phone(con)
  self.assertEqual(comms.get(con,'legal_phone'),'+7 900 000-00-00')
