import base64,struct,zlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from crm import db,stickerpacks,comms,smart

def png():
 def chunk(k,d):return struct.pack('>I',len(d))+k+d+struct.pack('>I',zlib.crc32(k+d)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\0\xff\0\xff'))+chunk(b'IEND',b'')
class UploadTest(unittest.TestCase):
 def setUp(self):
  self.con=db.connect(str(Path(tempfile.mkdtemp())/'upload.sqlite3'));db.init_db(self.con);self.addCleanup(self.con.close)
 def test_offline_upload_dedup_and_send(self):
  with patch.object(comms,'tg_call',side_effect=AssertionError('no Telegram import')):
   result=stickerpacks.upload_image(self.con,'Оля',base64.b64encode(png()).decode())
   self.assertEqual(stickerpacks.upload_image(self.con,'Оля',base64.b64encode(png()).decode())['added'],0)
  self.assertEqual(len(stickerpacks.catalog(self.con)['packs'][0]['stickers']),1)
  comms.put(self.con,'wazzup_key','FAKE');comms.put(self.con,'wazzup_tg_channel','test-tg')
  cid=comms.start_wazzup_username(self.con,'@synthetic_user')
  with patch.object(comms,'wz_call',return_value={'messageId':'confirmed'}) as provider:
   comms.send_sticker(self.con,cid,result['id'],{'name':'Synthetic'})
  self.assertIn('/api/sticker-image?',provider.call_args.args[3]['contentUri'])
  self.assertNotIn('text',provider.call_args.args[3])
 def test_invalid_uploads_leave_no_partial_pack(self):
  for value in ('bogus',base64.b64encode(b'<script>').decode(),base64.b64encode(png()[:-2]).decode()):
   with self.assertRaises(comms.CommsError):stickerpacks.upload_image(self.con,'Pack',value)
  self.assertEqual(self.con.execute('SELECT count(*) FROM sticker_packs').fetchone()[0],0)
 def test_blank_email_does_not_block_sale_or_erase_existing(self):
  from crm import services as sv
  user={'id':1,'role':'admin','name':'Test'}
  sid=sv.create_student(self.con,{'name':'Test Child','birth':'2020-01-01','parent_name':'Parent','phone':'+79000000001'},user)
  with patch.object(smart,'REQUIRE',True):smart.require(self.con,sid,'sale')
  self.assertIn('email',smart.required_info(self.con,sid,'sale')['missing'])
  smart.fill_required(self.con,sid,{'email':'a@example.com'},user);smart.fill_required(self.con,sid,{'email':''},user)
  self.assertEqual(smart.required_info(self.con,sid,'sale')['values']['email'],'a@example.com')
  with self.assertRaises(sv.ApiError):smart.fill_required(self.con,sid,{'email':'bad'},user)
