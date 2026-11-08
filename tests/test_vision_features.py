"""Test data and permission checks for diagnostics, statistics, and recapture."""
import _bootstrap  # Resolve application imports from any working directory.
from dataclasses import replace
from datetime import datetime,timezone
import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
import test_admin_terminal as fixtures
from test_registration import vector,FakeRecognizer
from admin_commands import CommandEngine,SessionGate,PresenceTracker
from profile_store import ProfileRepository
from recapture_ui import RecaptureSession
from vision_runtime import VisionDiagnostics


class NewRecognizer(FakeRecognizer):
    def feature(self,aligned):
        return vector(self.index)+.1*vector(12)


class FeatureTests(unittest.TestCase):
    setUp=fixtures.TerminalTests.setUp
    make_engine=fixtures.TerminalTests.make_engine

    def frame(self):
        frame=np.random.default_rng(0).integers(70,180,(480,640,3),dtype=np.uint8)
        face=np.zeros((1,15),np.float32);face[0,:4]=(100,100,150,150)
        return frame,face

    def gate(self,actor=None):
        now=[100.0]
        gate=SessionGate(clock=lambda:now[0])
        gate.observe(actor or self.admin);gate.open()
        return gate,now

    def collect(self,session,index=1):
        frame,faces=self.frame()
        for i in range(session.total):session.capture(frame,faces,NewRecognizer(index))
        self.assertEqual(session.snapshot()[0],session.total)

    def test_vision_commands_and_admin_only_threshold(self):
        vision=VisionDiagnostics()
        metrics=[dict(identity=self.user.identity,score=.81,blur=150,brightness=110,reason='MATCHED')]
        vision.record((480,640,3),metrics,30,now=100)
        vision.record((480,640,3),metrics,31,now=100.1)
        engine=CommandEngine(self.repo,self.admin.identity,lambda _:True,vision=vision)
        self.assertIn('640 x 480',engine.begin('vision status').text)
        self.assertAlmostEqual(vision.snapshot(now=100.2)['fps'],10)
        debug=engine.begin('vision debug on').text
        self.assertIn('0.810',debug);self.assertIn('Deniz',debug)
        self.assertTrue(vision.snapshot()['debug'])
        engine.begin('vision debug off');self.assertFalse(vision.snapshot()['debug'])
        engine.begin('vision threshold 0.72');self.assertEqual(vision.snapshot()['threshold'],.72)
        self.assertIn('0.720',engine.begin('vision threshold').text)
        for value in ('nan','inf','0','-1','1.1','bad'):
            with self.assertRaises(ValueError):engine.begin('vision threshold '+value)
        sup=CommandEngine(self.repo,self.sup.identity,lambda _:True,vision=vision)
        with self.assertRaises(ValueError):sup.begin('vision threshold .2')
        self.assertEqual(vision.snapshot()['threshold'],.72)
        vision.fail('test camera error')
        self.assertIn('test camera error',engine.begin('vision status').text)

    def test_presence_stats_survive_name_change_and_restart(self):
        tracker=PresenceTracker(self.repo)
        tracker.observe([self.user.identity],100)
        tracker.observe([self.user.identity],105)
        self.assertEqual(tracker.current(self.user.identity,now=109),9)
        self.assertEqual(self.repo.stats(self.user.identity)['total'],1)
        tracker.expire(115)
        self.assertIsNone(tracker.current(self.user.identity,now=115))
        tracker.observe([self.user.identity],116)
        self.engine.begin('user edit Deniz name Ada')
        self.assertEqual(self.repo.stats(self.user.identity)['total'],2)
        engine=CommandEngine(self.repo,self.admin.identity,lambda _:True,presence=tracker)
        self.assertIn('TOTAL LOGINS',engine.begin('user Ada stats').text)
        tracker.close()
        other=ProfileRepository(self.directory,self.legacy)
        self.assertEqual(other.stats(self.user.identity)['total'],2)
        self.assertEqual(other.stats(self.user.identity)['today'],2)

    def test_today_uses_turkey_timezone(self):
        with self.repo.connect() as conn:
            for session_id,stamp in [('a','2026-10-08T20:59:00+00:00'),('b','2026-10-08T21:05:00+00:00')]:
                conn.execute('INSERT INTO login_sessions VALUES(?,?,?,?,?,?)',(session_id,self.user.identity,stamp,stamp,stamp,'live'))
        stats=self.repo.stats(self.user.identity,datetime(2026,10,9,6,tzinfo=timezone.utc))
        self.assertEqual(stats['total'],2);self.assertEqual(stats['today'],1)
        self.assertEqual(stats['first_today'],'2026-10-08T21:05:00+00:00')

    def test_legacy_login_import_is_idempotent(self):
        self.repo.log('Deniz','LOGIN','Deniz')
        with self.repo.connect() as conn:conn.execute("DELETE FROM metadata WHERE key='login_history_v1'")
        other=ProfileRepository(self.directory,self.legacy)
        self.assertEqual(other.stats(self.user.identity)['legacy'],1)
        self.assertEqual(ProfileRepository(self.directory,self.legacy).stats(self.user.identity)['total'],1)

    def test_partial_and_cancel_preserve_original(self):
        plan=self.engine.begin('user Deniz recapture').plan
        session=RecaptureSession(self.repo,plan,self.admin.identity)
        path=self.repo._source(self.user.identity);before=path.read_bytes()
        frame,faces=self.frame()
        session.capture(frame,faces,NewRecognizer(1))
        self.assertEqual(path.read_bytes(),before)
        gate,_=self.gate()
        with self.assertRaises(ValueError):session.commit(gate)
        session.cancel()
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse((self.directory.parent/'backups').exists())

    def test_complete_recapture_backs_up_and_preserves_profile(self):
        old=self.repo.find('Deniz');path=self.repo._source(old.identity);before=path.read_bytes()
        plan=self.engine.begin('user Deniz recapture').plan
        session=RecaptureSession(self.repo,plan,self.admin.identity)
        self.collect(session)
        self.assertEqual(path.read_bytes(),before)
        gate,_=self.gate();session.commit(gate)
        self.assertEqual(session.backup.read_bytes(),before)
        new=self.repo.find('Deniz')
        self.assertFalse(np.array_equal(old.templates,new.templates))
        self.assertEqual((new.name,new.role,new.fields,new.created_at,new.last_login),
                         (old.name,old.role,old.fields,old.created_at,old.last_login))
        self.assertEqual(new.revision,old.revision+1)
        self.assertEqual(len(new.templates),3)
        self.assertEqual(ProfileRepository(self.directory,self.legacy).find('Deniz').revision,new.revision)
        with self.assertRaises(ValueError):session.commit(gate)

    def test_remove_deletes_only_the_target_backups_and_sessions(self):
        session=RecaptureSession(self.repo,self.engine.begin('user Deniz recapture').plan,self.admin.identity)
        self.collect(session);gate,_=self.gate();session.commit(gate)
        target_backup=session.backup
        other_dir=self.repo._backup_dir(self.op.identity);other_dir.mkdir(parents=True)
        other_backup=other_dir/'keep.npz';other_backup.write_bytes(b'other profile backup')
        self.repo.mark_login(self.user.identity)
        self.engine.begin('user Deniz remove');self.engine.reply('y')
        self.assertFalse(target_backup.exists())
        self.assertTrue(other_backup.exists())
        self.assertEqual(self.repo.stats(self.user.identity)['total'],0)

    def test_six_sample_legacy_profile_remains_six(self):
        with TemporaryDirectory() as tmp:
            enrollment=importlib.import_module('face_enrollment')
            path=Path(tmp)/'admin.npz';enrollment.save_admin([vector(10)]*6,path)
            before=path.read_bytes()
            repo=ProfileRepository(Path(tmp)/'people',path)
            actor=repo.get('Admin');engine=CommandEngine(repo,'Admin',lambda _:True)
            session=RecaptureSession(repo,engine.begin('user Admin recapture').plan,'Admin')
            self.assertEqual(session.total,6);self.collect(session,index=10)
            gate,_=self.gate(actor);session.commit(gate)
            self.assertEqual(len(enrollment.load_admin(path)),6)
            self.assertEqual(session.backup.read_bytes(),before)

    def test_wrong_face_multiple_faces_and_expiry(self):
        plan=self.engine.begin('user Deniz recapture').plan
        now=[0.0];session=RecaptureSession(self.repo,plan,self.admin.identity,clock=lambda:now[0])
        frame,faces=self.frame()
        session.capture(frame,faces,NewRecognizer(3))
        session.capture(frame,np.repeat(faces,2,axis=0),NewRecognizer(1))
        self.assertEqual(session.snapshot()[0],0)
        now[0]=300
        with self.assertRaises(ValueError):session.capture(frame,faces,NewRecognizer(1))

    def test_save_requires_original_manager_freshly_recognized(self):
        path=self.repo._source(self.user.identity);before=path.read_bytes()
        session=RecaptureSession(self.repo,self.engine.begin('user Deniz recapture').plan,self.admin.identity)
        self.collect(session)
        gate,now=self.gate();gate.observe(self.user)
        with self.assertRaises(ValueError):session.commit(gate)
        self.assertEqual(path.read_bytes(),before)
        gate.observe(self.sup)
        with self.assertRaises(ValueError):session.commit(gate)
        gate.observe(self.admin);session.commit(gate)
        self.assertTrue(session.saved)

    def test_supervisor_needs_sudo_and_reauth_after_lock(self):
        engine=self.make_engine(self.sup)
        with self.assertRaises(ValueError):engine.begin('user Deniz recapture')
        with self.assertRaises(ValueError):engine.begin('sudo user Admin recapture')
        self.assertEqual(engine.begin('sudo user Deniz recapture').request,'password')
        result=engine.reply('test-SUPERVISOR');self.assertEqual(result.action,'recapture')
        session=RecaptureSession(self.repo,result.plan,self.sup.identity)
        self.collect(session)
        gate,now=self.gate(self.sup);gate.observe(self.user);gate.observe(self.sup)
        with self.assertRaises(ValueError):session.commit(gate,'test-ADMIN',self.access)
        session.commit(gate,'test-SUPERVISOR',self.access)
        self.assertTrue(session.saved)

    def test_revision_change_and_actor_revocation_block_save(self):
        session=RecaptureSession(self.repo,self.engine.begin('user Deniz recapture').plan,self.admin.identity)
        self.collect(session)
        self.engine.begin('user edit Deniz field Design')
        gate,_=self.gate()
        with self.assertRaises(ValueError):session.commit(gate)
        fresh=RecaptureSession(self.repo,self.engine.begin('user Deniz recapture').plan,self.admin.identity)
        self.collect(fresh)
        with self.repo.connect() as conn:
            conn.execute('UPDATE profiles SET active=0 WHERE identity=?',(self.admin.identity,))
        with self.assertRaises(ValueError):fresh.commit(gate)

    def test_final_commit_rejects_injected_other_person_samples(self):
        plan=self.engine.begin('user Deniz recapture').plan
        before=self.repo._source(self.user.identity).read_bytes()
        with self.assertRaises(ValueError):self.repo.replace_templates(plan,self.admin.identity,[vector(3)]*3)
        self.assertEqual(self.repo._source(self.user.identity).read_bytes(),before)


if __name__=='__main__':unittest.main()
