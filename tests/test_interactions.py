import _bootstrap  # Resolve application imports from any working directory.
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import wave
import numpy as np
from activity_feed import ActivityFeed
from soft_sounds import SoftSounds,SOUND_DIR
from vision_hud import face_frame,BLUE
from admin_commands import CommandEngine
from person_store import save_person
from profile_store import ProfileRepository
from test_registration import vector


class FeedTests(unittest.TestCase):
    def test_event_transitions_debounce_and_bounded_cursor(self):
        feed=ActivityFeed(4)
        profile=SimpleNamespace(name='Admin',role='ADMIN',active=True)
        profiles={'one':profile}
        feed.observe(['one'],profiles,0)
        for index in range(1,20):feed.observe(['one'],profiles,index*.02)
        self.assertEqual(len(feed.read()[0]),1)
        feed.observe([],profiles,.5);feed.observe(['one'],profiles,.6)
        self.assertEqual(len(feed.read()[0]),1)
        feed.observe([],profiles,.7);feed.observe([],profiles,1.1)
        feed.observe(['one'],profiles,1.2)
        lines,cursor=feed.read()
        self.assertIn('lost',lines[-2]);self.assertIn('reconnected',lines[-1])
        self.assertEqual(feed.read(cursor)[0],[])
        for index in range(10):feed.add(f'event {index}')
        lines,latest=feed.read(cursor)
        self.assertEqual(len(lines),4);self.assertGreater(latest,cursor)

    def test_unknown_multi_and_camera_error_not_spammed(self):
        feed=ActivityFeed()
        feed.observe(['unknown'],{},0);feed.observe(['unknown'],{},1)
        self.assertEqual(feed.read()[0],[])
        feed.observe(['one','two'],{},2);feed.observe(['one','two'],{},3)
        feed.fail('offline');feed.fail('offline')
        self.assertEqual(len(feed.read()[0]),2)


class SoundAndFrameTests(unittest.TestCase):
    def test_cues_are_short_and_quiet(self):
        for path in SOUND_DIR.glob('*.wav'):
            with wave.open(str(path),'rb') as source:
                self.assertEqual(source.getnchannels(),1)
                self.assertLess(source.getnframes()/source.getframerate(),.35)
                samples=np.frombuffer(source.readframes(source.getnframes()),dtype='<i2')
                self.assertGreater(np.max(np.abs(samples)),0)
                self.assertLess(np.max(np.abs(samples))/32767,.04)
        self.assertEqual(len(list(SOUND_DIR.glob('*.wav'))),3)

    @patch('soft_sounds.winsound.PlaySound')
    def test_sound_cues_do_not_repeat_on_short_reconnect_or_block(self,play):
        sounds=SoftSounds()
        sounds.observe('one','ACQUIRING',0,0)
        sounds.observe('one','ASSEMBLING',.25,.2)
        sounds.observe('one','ASSEMBLING',.3,.4)
        sounds.observe('one','GREETING',1,3)
        sounds.observe(None,'SUSPENDED',0,4)
        sounds.observe('one','READY',1,4.5)
        self.assertEqual(play.call_count,2)
        for call in play.call_args_list:self.assertTrue(call.args[1]&0x1)  # SND_ASYNC
        sounds.set_enabled(False)
        play.reset_mock();sounds.play('panel',5)
        play.assert_not_called()

    def test_role_frame_details_are_distinct_and_do_not_paint_face_center(self):
        outputs=[]
        for role in ('USER','OPERATOR','SUPERVISOR','ADMIN'):
            canvas=np.full((320,320,3),80,np.uint8)
            face_frame(canvas,40,40,280,280,BLUE,role)
            np.testing.assert_array_equal(canvas[90:230,90:230],80)
            outputs.append(canvas)
        for previous,current in zip(outputs,outputs[1:]):
            self.assertFalse(np.array_equal(previous,current))


class InteractionCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        directory=Path(self.temp.name)/'people';legacy=Path(self.temp.name)/'none.npz'
        for index,role in enumerate(('ADMIN','SUPERVISOR','USER')):
            save_person(role,('Software',),role,[vector(index)]*3,directory,legacy)
        self.repo=ProfileRepository(directory,legacy)
        self.allowed=True
        self.engine=CommandEngine(self.repo,self.repo.find('ADMIN').identity,lambda _:self.allowed)

    @patch('soft_sounds.winsound.PlaySound')
    def test_new_commands_help_and_guard(self,play):
        for command in ('watch','vision sound'):
            self.assertIn(command,self.engine.begin('help').text)
        self.assertEqual(self.engine.begin('watch').action,'watch')
        self.assertIn('OFF',self.engine.begin('vision sound off').text)
        self.assertNotIn('vision nod',self.engine.begin('help').text)
        self.assertNotIn('nod_enabled',self.engine.vision.snapshot())
        self.assertIn('QUIET SOUNDS',self.engine.begin('vision status').text)
        for command in ('sudo watch','watch extra','vision nod','vision nod on','vision nod off','vision sound 8'):
            with self.assertRaises(ValueError):self.engine.begin(command)
        self.allowed=False
        with self.assertRaises(ValueError):self.engine.begin('watch')
        with self.assertRaises(ValueError):self.engine.begin('vision sound on')

    def test_supervisor_still_cannot_modify_admin(self):
        engine=CommandEngine(self.repo,self.repo.find('SUPERVISOR').identity,lambda _:True)
        for command in ('sudo user edit ADMIN name Replacement','sudo user ADMIN remove',
                         'sudo user edit USER role ADMIN'):
            with self.assertRaises(ValueError):engine.begin(command)
