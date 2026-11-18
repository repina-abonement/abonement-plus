import json,os,sqlite3,time,threading,unittest
from unittest.mock import patch
from crm import db,comms,wazzupqueue,wazzuptrace
from tests.test_wazzup_incoming_recovery import IncomingRecovery


class DurableInbox(unittest.TestCase):
    setUp=IncomingRecovery.setUp
    hook=IncomingRecovery.hook

    def test_main_database_locked_event_accepted_and_later_visible_once(self):
        other=db.connect(self.con.execute('PRAGMA database_list').fetchone()[2]);self.addCleanup(other.close)
        other.execute('BEGIN IMMEDIATE')
        try:
            start=time.monotonic();status,body=self.hook(drain=False)
            self.assertLess(time.monotonic()-start,2)
            self.assertEqual(status,200);self.assertTrue(body['queued'])
            self.assertEqual(wazzupqueue.process_pending(self.con)['deferred'],1)
            self.assertEqual(wazzupqueue.stats(self.con)['pending'],1)
            self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        finally:other.execute('ROLLBACK')
        self.assertEqual(wazzupqueue.process_pending(self.con,force=True)['processed'],1)
        self.hook()
        self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT unread FROM conversations').fetchone()[0],1)
        self.assertEqual(wazzupqueue.stats(self.con)['pending'],0)
        self.assertEqual(len(comms.inbox(self.con,q='7942')['conversations']),1)

    def test_full_payload_survives_restart_and_is_scrubbed_after_processing(self):
        self.payload['messages'][0]['text']='Synthetic full payload '*3000
        status,body=self.hook(drain=False)
        path=self.con.execute('PRAGMA database_list').fetchone()[2]
        reopened=db.connect(path);self.addCleanup(reopened.close)
        self.assertEqual(wazzupqueue.process_pending(reopened)['processed'],1)
        self.assertEqual(reopened.execute('SELECT text FROM messages').fetchone()[0],self.payload['messages'][0]['text'])
        with wazzupqueue.connection(reopened) as q:
            row=q.execute('SELECT * FROM events').fetchone()
            self.assertEqual(row['payload'],'');self.assertEqual(row['state'],'done')
        self.assertEqual(os.stat(wazzupqueue.path_for(reopened)).st_mode&0o777,0o600)

    def test_crash_after_main_commit_before_ack_replays_without_duplicate(self):
        eid=wazzupqueue.enqueue(self.con,self.payload)
        self.con.execute('BEGIN IMMEDIATE');comms.wz_hook(self.con,self.payload);self.con.execute('COMMIT')
        with wazzupqueue.connection(self.con) as q:
            q.execute("UPDATE events SET state='processing',lease_until=0 WHERE id=?",(eid,))
        self.assertEqual(wazzupqueue.process_pending(self.con)['processed'],1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT unread FROM conversations').fetchone()[0],1)

    def test_queue_storage_failure_is_not_acknowledged(self):
        with patch.object(wazzupqueue,'enqueue',side_effect=OSError('Synthetic disk full')):
            status,body=self.hook(drain=False)
        self.assertEqual(status,503);self.assertFalse(body['ok'])
        self.assertTrue(wazzupqueue.health(self.con)['incoming_storage_error'])
        self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],0)

    def test_worker_restarts_and_processes_without_new_provider_delivery(self):
        self.hook(drain=False)
        path=self.con.execute('PRAGMA database_list').fetchone()[2]
        stop=threading.Event();worker=wazzupqueue.start_worker(lambda:db.connect(path),stop)
        try:
            for _ in range(100):
                if self.con.execute('SELECT count(*) FROM messages').fetchone()[0]:break
                time.sleep(.02)
            self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        finally:
            stop.set();wazzupqueue._WAKE.set();worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_authentication_happens_before_queue_storage(self):
        comms.put(self.con,'hook_token','different-secret')
        status,body=self.hook(drain=False)
        self.assertEqual(status,403)
        self.assertFalse(os.path.exists(wazzupqueue.path_for(self.con)))

    def test_trace_finds_saved_event_even_before_main_processing(self):
        self.hook(drain=False)
        before=self.con.total_changes
        r=wazzuptrace.trace(self.con,'synthetic-incoming-id')
        self.assertEqual(r['state'],'saved_waiting_for_processing')
        self.assertEqual(r['durable_receipts'][0]['state'],'pending')
        self.assertNotIn('Synthetic incoming message',str(r))
        self.assertEqual(self.con.total_changes,before)

    def test_capacity_failure_preserves_previously_saved_events(self):
        eid=wazzupqueue.enqueue(self.con,self.payload)
        different=json.loads(json.dumps(self.payload));different['messages'][0]['messageId']='another-synthetic'
        with patch.object(wazzupqueue,'MAX_PENDING_BYTES',1):
            with self.assertRaises(OSError):wazzupqueue.enqueue(self.con,different)
        self.assertEqual(wazzupqueue.message_receipts(self.con,'synthetic-incoming-id')[0]['queue_id'],eid)
        self.assertEqual(wazzupqueue.stats(self.con)['pending'],1)

    def test_late_recovery_and_old_echo_preserve_newest_preview_and_unread(self):
        self.payload['messages'][0].update(dateTime='2026-11-18T10:06:00Z',text='Synthetic newest')
        self.hook()
        self.payload['messages'][0].update(messageId='old-echo',dateTime='2026-11-18T07:24:00Z',isEcho=True,text='Synthetic old reply')
        self.hook()
        row=self.con.execute('SELECT * FROM conversations').fetchone()
        self.assertEqual(row['unread'],1);self.assertEqual(row['last_text'],'Synthetic newest')
        self.payload['messages'][0].update(messageId='recovered-older-incoming',dateTime='2026-11-18T09:55:00Z',isEcho=False,text='Synthetic recovered')
        self.hook()
        row=self.con.execute('SELECT * FROM conversations').fetchone()
        self.assertEqual(row['unread'],2);self.assertEqual(row['last_text'],'Synthetic newest')

    def test_done_events_cleanup_keeps_pending_events(self):
        done=wazzupqueue.enqueue(self.con,self.payload)
        wazzupqueue.process_pending(self.con)
        self.payload['messages'][0]['messageId']='still-pending'
        pending=wazzupqueue.enqueue(self.con,self.payload)
        with wazzupqueue.connection(self.con) as q:
            q.execute('UPDATE events SET done_at=0 WHERE id=?',(done,))
        wazzupqueue.process_pending(self.con,limit=0)
        self.assertEqual(wazzupqueue.message_receipts(self.con,'synthetic-incoming-id'),[])
        self.assertEqual(wazzupqueue.message_receipts(self.con,'still-pending')[0]['queue_id'],pending)

    def test_worker_recovers_when_first_database_connection_fails(self):
        self.hook(drain=False)
        path=self.con.execute('PRAGMA database_list').fetchone()[2]
        calls=[]
        def connect():
            calls.append(1)
            if len(calls)==1:raise sqlite3.OperationalError('Synthetic temporary lock')
            return db.connect(path)
        stop=threading.Event();worker=wazzupqueue.start_worker(connect,stop)
        try:
            for _ in range(200):
                if self.con.execute('SELECT count(*) FROM messages').fetchone()[0]:break
                time.sleep(.02)
            self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],1)
            self.assertGreaterEqual(len(calls),2)
        finally:
            stop.set();wazzupqueue._WAKE.set();worker.join(3)

    def test_two_workers_claim_same_event_once(self):
        self.hook(drain=False)
        path=self.con.execute('PRAGMA database_list').fetchone()[2]
        barrier=threading.Barrier(2);errors=[]
        def run():
            con=db.connect(path)
            try:
                barrier.wait();wazzupqueue.process_pending(con)
            except Exception as error:errors.append(type(error).__name__)
            finally:con.close()
        threads=[threading.Thread(target=run) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(3)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors,[])
        self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT unread FROM conversations').fetchone()[0],1)
