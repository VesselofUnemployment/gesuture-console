"""Regression cases from the review: real temporary storage, no camera/input."""
import _bootstrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch
import importlib
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
import numpy as np
import test_admin_terminal as fixtures
from test_registration import vector
from person_store import save_person
from profile_store import ProfileRepository
from admin_commands import MutationPlan, SessionGate
from recognition_presence import RecognitionPresence
from recognition_ui import RecognitionWindow
from runtime_lifecycle import CameraLease, cleanup_steps
from presence_writer import AsyncPresenceTracker
from vision_runtime import VisionDiagnostics
from latest_frames import LatestFrames
from project_paths import PROJECT_ROOT


class StorageTests(unittest.TestCase):
    setUp = fixtures.TerminalTests.setUp
    make_engine = fixtures.TerminalTests.make_engine

    def test_readonly_inspection_preserves_database_bytes(self):
        before = self.repo.db.read_bytes()
        self.assertEqual(len(ProfileRepository.inspect_people(self.directory,self.legacy)),4)
        self.assertEqual(self.repo.db.read_bytes(),before)

    def test_readonly_inspection_does_not_create_storage(self):
        directory = self.directory.parent/'new-storage'/'people'
        self.assertEqual(ProfileRepository.inspect_people(directory,self.legacy),[])
        self.assertFalse(directory.parent.exists())

    def test_invalid_access_configuration_has_controlled_errors(self):
        from person_store import role_for_code
        for value in ([], None, {}, dict(version=1,algorithm='pbkdf2_sha256',iterations=100000,roles=[]),
                      dict(version=1,algorithm='pbkdf2_sha256',iterations=100000,roles={'OPERATOR':None})):
            self.access.write_text(json.dumps(value),encoding='utf-8')
            with self.assertRaises(ValueError):role_for_code('synthetic-test-code',self.access)

    def test_repeated_reads_do_not_reopen_embeddings_or_take_write_lock(self):
        self.repo.get(self.admin.identity)
        with self.repo.connect() as writer:
            writer.execute('BEGIN IMMEDIATE')
            with patch('numpy.load', wraps=np.load) as load:
                for _ in range(100):
                    self.assertEqual(self.repo.get(self.admin.identity).role, 'ADMIN')
                self.assertEqual(load.call_count, 0)

    def test_external_revocation_is_immediate_with_warm_cache(self):
        self.repo.get(self.admin.identity)
        with self.repo.connect() as conn:
            conn.execute('UPDATE profiles SET active=0,revision=revision+1 WHERE identity=?', (self.admin.identity,))
        current = self.repo.get(self.admin.identity)
        self.assertFalse(current.active)
        self.assertEqual(current.revision, self.admin.revision + 1)

    def test_corrupt_source_is_excluded_without_losing_metadata(self):
        path = self.repo._source(self.user.identity)
        path.write_bytes(b'broken zip')
        people = self.repo.people()
        self.assertNotIn(self.user.identity, [person.identity for person in people])
        self.assertIsNone(self.repo.get(self.user.identity))
        self.assertEqual(len(people), 3)
        self.assertIn(path.name, self.repo.record_errors)
        with self.repo.connect() as conn:
            self.assertIsNotNone(conn.execute('SELECT * FROM profiles WHERE identity=?', (self.user.identity,)).fetchone())
        with self.assertRaises((ValueError, RuntimeError)):
            save_person('Other', ('Software',), 'USER', [vector(7)]*3, self.directory, self.legacy)

    def test_missing_source_does_not_delete_metadata(self):
        path = self.repo._source(self.user.identity)
        moved = path.with_suffix('.held')
        path.rename(moved)
        self.assertIsNone(self.repo.get(self.user.identity))
        self.repo.people()
        moved.rename(path)
        self.assertEqual(self.repo.get(self.user.identity).name, self.user.name)

    def test_bad_metadata_cannot_grant_access_or_break_other_profiles(self):
        with self.repo.connect() as conn:
            conn.execute("UPDATE profiles SET role='INVALID' WHERE identity=?", (self.user.identity,))
        self.assertIsNone(self.repo.get(self.user.identity))
        self.assertEqual(len(self.repo.people()), 3)

    def test_thread_registration_race_has_one_winner(self):
        barrier = threading.Barrier(2)
        def save():
            barrier.wait()
            try:
                save_person('Parallel', ('Software',), 'USER', [vector(7)]*3, self.directory, self.legacy)
                return 'saved'
            except ValueError:
                return 'rejected'
        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda _: save(), range(2)))
        self.assertCountEqual(results, ['saved', 'rejected'])
        self.assertEqual(len(self.repo.people()), 5)

    def child(self, code, *args):
        prefix = 'import sys;sys.path.insert(0,' + repr(str(PROJECT_ROOT/'src')) + ');'
        return subprocess.run([sys.executable, '-B', '-c', prefix+code, *map(str,args)],
                              capture_output=True, timeout=15)

    def test_process_registration_race_has_one_winner(self):
        gate = self.directory.parent/'go'
        code = ("import time;from pathlib import Path;from person_store import save_person;import numpy as np;"
                "v=np.zeros(128,np.float32);v[8]=1;"
                "exec('while not Path(sys.argv[3]).exists(): time.sleep(.005)');"
                "save_person('Processes',('Software',),'USER',[v]*3,Path(sys.argv[1]),Path(sys.argv[2]))")
        prefix = 'import sys;sys.path.insert(0,' + repr(str(PROJECT_ROOT/'src')) + ');'
        processes = [subprocess.Popen([sys.executable,'-B','-c',prefix+code,str(self.directory),str(self.legacy),str(gate)],
                                      stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
        try:
            gate.touch()
            for process in processes:
                process.communicate(timeout=15)
            self.assertEqual(sum(p.returncode==0 for p in processes), 1)
            self.assertEqual(len(self.repo.people()), 5)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_remove_recovers_after_abrupt_process_exit(self):
        path = self.repo._source(self.user.identity)
        before = path.read_bytes()
        code = """
import os
from pathlib import Path
from unittest.mock import patch
from profile_store import ProfileRepository
from admin_commands import MutationPlan
repo=ProfileRepository(Path(sys.argv[1]),Path(sys.argv[2]))
actor=repo.find('Admin');target=repo.find('Deniz');source=repo._source(target.identity)
rename=Path.rename
def crash(path,destination):
    result=rename(path,destination)
    if path==source:os._exit(41)
    return result
with patch.object(Path,'rename',crash):
    repo.mutate(MutationPlan('remove',target.identity,target.name,target.revision,actor_role='ADMIN'),actor.identity)
"""
        result = self.child(code, self.directory, self.legacy)
        self.assertEqual(result.returncode, 41, result.stderr.decode(errors='replace'))
        self.repo.people()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.repo.get(self.user.identity).name, self.user.name)
        self.assertFalse(list((self.directory.parent/'file-journal').glob('*.json')))

    def test_recapture_recovers_original_after_abrupt_process_exit(self):
        path = self.repo._source(self.user.identity)
        before = path.read_bytes()
        code = """
import os,numpy as np
from pathlib import Path
from unittest.mock import patch
from profile_store import ProfileRepository
from admin_commands import MutationPlan
repo=ProfileRepository(Path(sys.argv[1]),Path(sys.argv[2]))
actor=repo.find('Admin');target=repo.find('Deniz');source=repo._source(target.identity)
samples=target.templates.copy();samples[:,12]=.05;samples/=np.linalg.norm(samples,axis=1)[:,None]
replace=Path.replace
def crash(path,destination):
    result=replace(path,destination)
    if Path(destination)==source:os._exit(42)
    return result
with patch.object(Path,'replace',crash):
    repo.replace_templates(MutationPlan('recapture',target.identity,target.name,target.revision,actor_role='ADMIN'),actor.identity,samples)
"""
        result = self.child(code, self.directory, self.legacy)
        self.assertEqual(result.returncode, 42, result.stderr.decode(errors='replace'))
        self.repo.people()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.repo.get(self.user.identity).revision, self.user.revision)

    def test_committed_remove_finishes_cleanup_after_restart(self):
        recover = self.repo.journal.recover
        def interrupt(conn):
            if conn.execute('SELECT COUNT(*) FROM file_commits').fetchone()[0]:
                raise KeyboardInterrupt()
            recover(conn)
        plan = MutationPlan('remove',self.user.identity,self.user.name,self.user.revision,actor_role='ADMIN')
        with patch.object(self.repo.journal,'recover',side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.repo.mutate(plan,self.admin.identity)
        restarted = ProfileRepository(self.directory,self.legacy)
        self.assertIsNone(restarted.get(self.user.identity))
        self.assertFalse(self.repo._source(self.user.identity).with_suffix('.removing').exists())

    def test_committed_recapture_is_not_rolled_back_during_recovery(self):
        recover = self.repo.journal.recover
        def interrupt(conn):
            if conn.execute('SELECT COUNT(*) FROM file_commits').fetchone()[0]:
                raise KeyboardInterrupt()
            recover(conn)
        plan = MutationPlan('recapture',self.user.identity,self.user.name,self.user.revision,actor_role='ADMIN')
        samples = self.user.templates.copy()
        samples[:,12] = .05
        samples /= np.linalg.norm(samples,axis=1)[:,None]
        with patch.object(self.repo.journal,'recover',side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.repo.replace_templates(plan,self.admin.identity,samples)
        restarted = ProfileRepository(self.directory,self.legacy)
        current = restarted.get(self.user.identity)
        self.assertEqual(current.revision, self.user.revision+1)
        np.testing.assert_allclose(current.templates,samples)

    def test_slow_storage_does_not_block_presence_observation(self):
        release = threading.Event()
        calls = []
        repo = SimpleNamespace(mark_login=lambda *args: (release.wait(2),calls.append(args)),
                               touch_login=lambda *args:None,end_login=lambda *args:None)
        tracker = AsyncPresenceTracker(repo)
        try:
            started = time.perf_counter()
            tracker.observe(['identity'],100)
            self.assertLess(time.perf_counter()-started,.1)
            self.assertEqual(tracker.current('identity',101),1)
        finally:
            release.set()
            deadline=time.monotonic()+3
            while not tracker.close() and time.monotonic()<deadline:time.sleep(.01)
        self.assertFalse(tracker.writer.thread.is_alive())
        self.assertEqual(len(calls),1)


class RuntimeTests(unittest.TestCase):
    def test_poll_error_revokes_authority_and_reschedules(self):
        scheduled=[];disabled=[]
        app=SimpleNamespace(closed=False,closing=False,diagnostics=VisionDiagnostics(),
            authority=RecognitionPresence(),gate=SessionGate(),app_gesture=SimpleNamespace(suppress=lambda:None),
            control=SimpleNamespace(enabled=True,set_enabled=lambda value:disabled.append(value)),
            notice=None,terminal=None,feed=SimpleNamespace(fail=lambda error:None),
            root=SimpleNamespace(after=lambda delay,callback:scheduled.append(delay)),
            _poll_once=lambda:(_ for _ in ()).throw(sqlite3.OperationalError('locked')))
        app.poll=lambda:None
        with self.assertLogs('vision',level='ERROR'):
            RecognitionWindow.poll(app)
        self.assertEqual(disabled,[False])
        self.assertEqual(len(scheduled),1)
        self.assertEqual(app.diagnostics.snapshot()['state'],'ERROR')

    def test_close_failure_does_not_skip_remaining_cleanup(self):
        calls=[]
        fail=lambda:(_ for _ in ()).throw(OSError('storage failed'))
        app=SimpleNamespace(closed=False,closing=False,shutdown_errors=[],stop=threading.Event(),
            authority=RecognitionPresence(),gate=SessionGate(),poll_id=None,termination_job=None,topmost_job=None,
            control=SimpleNamespace(close=fail),launcher=SimpleNamespace(close=lambda:calls.append('launcher')),
            sounds=SimpleNamespace(stop=lambda:calls.append('sounds')),notice=None,tray=None,
            recapture=None,terminal=None,worker=None,presence=SimpleNamespace(close=fail),
            root=SimpleNamespace(destroy=lambda:calls.append('destroy')))
        with self.assertLogs('vision',level='ERROR'):
            RecognitionWindow.close(app)
        self.assertTrue(app.closed)
        self.assertEqual(calls,['launcher','sounds','destroy'])
        RecognitionWindow.close(app)
        self.assertEqual(calls.count('destroy'),1)

    def test_camera_lease_blocks_second_owner_and_releases(self):
        with TemporaryDirectory() as directory:
            first=CameraLease(0,directory)
            try:
                with self.assertRaises(RuntimeError):CameraLease(0,directory)
                other=CameraLease(1,directory);other.close()
            finally:first.close()
            after=CameraLease(0,directory);after.close()

    def test_slow_camera_read_does_not_force_release(self):
        entered=threading.Event();release=threading.Event();stop=threading.Event()
        def read():
            entered.set();release.wait(2);return True,0
        frames=LatestFrames(SimpleNamespace(read=read),stop)
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(frames.close(timeout=.01))
            self.assertTrue(frames.thread.is_alive())
        finally:
            release.set();self.assertTrue(frames.close(timeout=1))

    def test_diagnostics_are_bounded_and_frame_age_uses_capture_time(self):
        diagnostics=VisionDiagnostics()
        for index in range(1000):diagnostics.set_latency(hand_ms=index)
        self.assertEqual(len(diagnostics.samples['hand_ms']),256)
        self.assertEqual(diagnostics.performance()['hand_ms']['count'],256)
        diagnostics.record((480,640,3),[],2,now=100,captured=99)
        self.assertEqual(diagnostics.snapshot(now=100)['age'],1)


if __name__=='__main__':unittest.main()
