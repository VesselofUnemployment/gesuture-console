import _bootstrap
from dataclasses import replace
import unittest
import numpy as np
from camera_idle import CameraIdle
from recognition_presence import RecognitionPresence
from admin_commands import SessionGate
from person_store import Person
from vision_runtime import VisionDiagnostics


class IdleTests(unittest.TestCase):
    def test_real_cover_like_noise_skips_models_and_bright_scene_wakes(self):
        idle=CameraIdle()
        dark=np.clip(np.random.default_rng(4).normal(16,1.3,(480,640,3)),0,255).astype(np.uint8)
        self.assertTrue(idle.update(dark))
        self.assertTrue(idle.update(dark))
        self.assertFalse(idle.update(np.full_like(dark,80)))

    def test_dim_textured_scene_and_saturated_blue_are_not_a_cover(self):
        idle=CameraIdle()
        frame=np.zeros((480,640,3),np.uint8);frame[:,320:]=45
        self.assertFalse(idle.update(frame))
        frame[:]=(60,0,0)
        self.assertFalse(idle.update(frame))

    def test_hysteresis_does_not_flicker(self):
        idle=CameraIdle();frame=np.full((480,640,3),28,np.uint8)
        self.assertFalse(idle.update(frame))
        self.assertTrue(idle.update(frame-5))
        self.assertTrue(idle.update(frame))
        self.assertFalse(idle.update(frame+10))
        self.assertFalse(idle.update(frame))

    def test_diagnostics_clear_faces_and_timing_when_entering_low_power(self):
        diagnostic=VisionDiagnostics()
        diagnostic.record((480,640,3),[{'identity':'a'}],40,0)
        diagnostic.set_camera_idle(True,dict(mean=16,p95=18,deviation=1))
        diagnostic.record((480,640,3),[],.3,1)
        state=diagnostic.snapshot(1)
        self.assertTrue(state['camera_paused'])
        self.assertEqual(state['state'],'PAUSED / DARK CAMERA')
        self.assertEqual(state['faces'],[])
        self.assertEqual(state['fps'],0)
        diagnostic.set_camera_idle(False,dict(mean=80,p95=100,deviation=15))
        diagnostic.record((480,640,3),[],20,1.25)
        self.assertEqual(diagnostic.snapshot(1.25)['state'],'ONLINE')


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.admin=Person('a','Manager',('Software',),'ADMIN',None)
        self.user=Person('u','Visitor',('Design',),'USER',None)
        self.profiles={p.identity:p for p in (self.admin,self.user)}

    def test_no_match_cannot_create_authority(self):
        presence=RecognitionPresence()
        self.assertIsNone(presence.observe(None,0,self.profiles))

    def test_loss_frames_do_not_extend_three_second_deadline(self):
        presence=RecognitionPresence()
        presence.observe(self.admin,100,self.profiles)
        for timestamp in (100.2,101,102.99):
            self.assertEqual(presence.observe(None,timestamp,self.profiles),self.admin)
        self.assertIsNone(presence.observe(None,103,self.profiles))
        self.assertIsNone(presence.observe(None,104,self.profiles))
        self.assertEqual(presence.observe(self.admin,105,self.profiles),self.admin)

    def test_different_user_multiple_faces_and_profile_change_cancel_old_authority(self):
        presence=RecognitionPresence()
        presence.observe(self.admin,1,self.profiles)
        self.assertEqual(presence.observe(self.user,1.1,self.profiles),self.user)
        presence.observe(self.admin,2,self.profiles)
        self.assertIsNone(presence.observe(None,2.1,self.profiles,unsafe=True))
        for changed in (replace(self.admin,role='USER'),replace(self.admin,active=False),replace(self.admin,revision=1)):
            presence.observe(self.admin,3,self.profiles)
            self.assertIsNone(presence.current(3.1,{'a':changed}))
        presence.observe(self.admin,4,self.profiles)
        self.assertIsNone(presence.current(4.1,{}))

    def test_button_and_open_keep_three_seconds_but_do_not_extend_owner_last_seen(self):
        gate=SessionGate(clock=lambda:100)
        gate.observe(self.admin,100)
        gate.observe(None,102.9)
        self.assertEqual(gate.current_candidate(102.99),self.admin)
        self.assertEqual(gate.open(102.99),'a')
        self.assertEqual(gate.last_seen,100)
        self.assertIsNone(gate.current_candidate(103))
        self.assertFalse(gate.valid('a',105))
        with self.assertRaises(ValueError):gate.open(103)

    def test_gate_other_person_or_multiple_faces_cancel_immediately(self):
        for person,unsafe in ((self.user,False),(None,True)):
            gate=SessionGate();gate.observe(self.admin,0);gate.open(0)
            gate.observe(person,.1,unsafe=unsafe)
            self.assertIsNone(gate.current_candidate(.1))
            self.assertIsNone(gate.owner)


if __name__=='__main__':unittest.main()
