"""Animation timing, current measured coordinates, and identity boundaries."""
import _bootstrap  # Resolve application imports from any working directory.
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from face_tracking import NODES, matches_face, mirror_points
from recognition_sequence import RecognitionSequence, triangulate, BUILD, BLINK, RETURN, GREETING


def fixture(role='ADMIN', identity='one'):
    profile = SimpleNamespace(name='admin', role=role, fields=('Software', 'Mechanics'), active=True)
    profiles = {identity: profile}
    face = np.array([160, 80, 240, 320], np.float32)
    results = [(face, identity, (255,185,65), .85)]
    rng = np.random.default_rng(4)
    points = rng.uniform((190,110), (370,350), (478,2)).astype(np.float32)
    return profiles, results, points


class AnimationTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((480,640,3), np.uint8)
        self.profiles, self.results, self.points = fixture()
        self.sequence = RecognitionSequence(np.random.default_rng(1))

    def step(self, now, points=True, results=None):
        results = self.results if results is None else results
        self.sequence.prepare(results, self.profiles, self.frame.shape, now)
        return self.sequence.render(self.frame, results, self.profiles,
                                    self.points if points else None, now)

    def started(self):
        self.step(0)
        self.step(.2)
        self.assertEqual(self.sequence.mesh_started, .2)

    def advance(self, target, points=True):
        start = self.sequence.last_time
        for now in np.arange(start+.1, target, .1):
            self.step(float(now), points)
        return self.step(target, points)

    def test_order_timing_and_fixed_square(self):
        first = self.step(0)
        self.assertTrue(first.any())
        self.assertIsNone(self.sequence.state(0)['profile'])
        self.step(.2)
        self.assertEqual(self.sequence.phase, 'ASSEMBLING')
        self.advance(.2+BUILD+.05)
        self.assertEqual(self.sequence.phase, 'BLINKING')
        self.advance(.2+BUILD+BLINK+.25)
        self.assertEqual(self.sequence.phase, 'RETURNING')
        state = self.sequence.state(self.sequence.last_time)
        self.assertGreater(state['expansion'], 0)
        self.assertIsNone(state['profile'])
        self.advance(.2+BUILD+BLINK+RETURN+.1)
        self.assertEqual(self.sequence.phase, 'GREETING')
        self.assertGreater(self.sequence.state(self.sequence.last_time)['greeting'], 0)
        self.advance(.2+BUILD+BLINK+RETURN+GREETING+.1)
        state = self.sequence.state(self.sequence.last_time)
        self.assertEqual(state['phase'], 'READY')
        self.assertEqual((state['square'],state['expansion'],state['mesh']), (1.,0.,0.))
        self.assertFalse(self.sequence.needs_mesh(self.sequence.last_time))

    def test_no_fake_mesh_without_measurements(self):
        for now in np.arange(0, 4, .1):self.step(float(now), points=False)
        self.assertIsNone(self.sequence.mesh_started)
        self.assertFalse(self.sequence.edges)
        self.assertEqual(self.sequence.phase, 'ACQUIRING')

    def test_short_measurement_loss_preserves_progress_without_stale_mesh(self):
        self.started();self.advance(.7)
        original = self.frame.copy()
        self.step(.8, points=False)
        self.assertEqual(self.sequence.mesh_started, .2)
        self.assertTrue(self.sequence.edges)
        paused = self.sequence.state(.8)['fraction']
        self.advance(2.8, points=False)
        self.assertEqual(self.sequence.state(2.8)['fraction'], paused)
        self.step(2.9)
        self.assertGreater(self.sequence.mesh_started,.2)
        self.assertTrue(np.array_equal(self.frame, original))

    def test_loss_identity_change_multi_and_revoked_reset(self):
        for kind in ('multiple','disabled','new'):
            with self.subTest(kind=kind):
                self.sequence.reset();self.started()
                if kind=='multiple':results=self.results*2
                elif kind=='disabled':
                    self.profiles['one'].active=False;results=self.results
                else:
                    self.profiles['two']=SimpleNamespace(name='Deniz',role='OPERATOR',fields=('Design',),active=True)
                    results=[(self.results[0][0],'two',None,.8)]
                self.step(.3,results=results)
                self.assertIsNone(self.sequence.mesh_started)
                self.assertFalse(self.sequence.edges)
                self.profiles['one'].active=True

    def test_short_identity_loss_pauses_and_return_resumes(self):
        self.started();self.advance(.7)
        order=list(self.sequence.edges)
        self.step(.8,points=False,results=[])
        self.assertEqual(self.sequence.phase,'SUSPENDED')
        self.assertFalse(self.sequence.visible)
        blank=self.step(2.8,points=False,results=[])
        np.testing.assert_array_equal(blank,self.frame)
        self.step(2.9)
        self.assertEqual(self.sequence.edges,order)
        self.assertEqual(self.sequence.phase,'ASSEMBLING')
        self.assertGreater(self.sequence.mesh_started,.2)

    def test_ready_returns_before_five_seconds_without_replaying_greeting(self):
        self.started();self.advance(6)
        self.step(6.1,points=False,results=[])
        self.step(8.1,points=False,results=[])
        self.step(10.99,points=False)
        state=self.sequence.state(10.99)
        self.assertEqual(state['phase'],'READY')
        self.assertEqual(state['greeting'],0)
        self.assertFalse(self.sequence.needs_mesh(10.99))

    def test_exact_five_seconds_restarts_even_without_intermediate_frames(self):
        self.started();self.advance(6)
        self.step(6.1,points=False,results=[])
        self.step(11,points=False,results=[])
        self.assertIsNone(self.sequence.signature)
        self.step(11.1)
        self.assertIsNone(self.sequence.mesh_started)
        self.sequence.reset();self.started()
        self.step(5.2)
        self.assertIsNone(self.sequence.mesh_started)

    def test_unknown_does_not_inherit_greeting_or_old_profile(self):
        self.started();self.advance(3.5)
        unknown=[(self.results[0][0],'Unknown',None,.2)]
        with patch.object(self.sequence,'caption') as caption:
            self.step(3.6,results=unknown)
            self.assertFalse(caption.called)
        self.assertFalse(self.sequence.visible)
        self.step(5.6)
        self.assertEqual(self.sequence.phase,'GREETING')

    def test_five_seconds_of_missing_landmarks_reacquires(self):
        self.started();self.advance(.7)
        self.step(.8,points=False)
        self.advance(5.9,points=False)
        self.assertIsNone(self.sequence.mesh_started)
        self.assertFalse(self.sequence.edges)

    def test_triangle_order_stays_fixed_but_coordinates_move(self):
        self.started();before=self.advance(.8)
        order=list(self.sequence.edges)
        self.points[:,0]+=24
        after=self.step(.8)
        self.assertEqual(order,self.sequence.edges)
        self.assertFalse(np.array_equal(before,after))
        self.assertEqual(set(order),set(triangulate(self.points[list(NODES)])))

    def test_roles_and_current_name(self):
        for role in ('USER','OPERATOR','SUPERVISOR','ADMIN'):
            with self.subTest(role=role):
                self.profiles['one'].role=role
                self.sequence.reset();self.started()
                self.advance(.2+BUILD+BLINK+RETURN+.15)
                with patch.object(self.sequence,'caption') as caption:
                    self.step(self.sequence.last_time+.1)
                    messages=[call.args[1] for call in caption.call_args_list]
                    if role=='USER':self.assertFalse(messages)
                    else:
                        self.assertIn('WELCOME BACK, admin',messages)
                        extra=caption.call_args.kwargs['extra']
                        self.assertEqual(extra,'SYSTEM AUTHORITY RESTORED' if role in ('ADMIN','SUPERVISOR') else '')

    def test_metadata_change_and_resolution_restarts(self):
        self.started()
        self.profiles['one'].name='Yeni_ad'
        self.step(.3)
        self.assertEqual(self.sequence.signature[1],'Yeni_ad')
        self.assertIsNone(self.sequence.mesh_started)
        self.frame=np.zeros((720,960,3),np.uint8)
        self.step(.4)
        self.assertIsNone(self.sequence.mesh_started)

    def test_mirror_and_face_association(self):
        original=self.points.copy()
        mirrored=mirror_points(self.points,640)
        np.testing.assert_allclose(mirrored[:,0],639-original[:,0])
        np.testing.assert_array_equal(self.points,original)
        self.assertTrue(matches_face(self.points,self.results[0][0]))
        self.assertFalse(matches_face(self.points,np.array([450,20,100,100])))
        invalid=self.points.copy();invalid[0,0]=np.nan
        self.assertFalse(matches_face(invalid,self.results[0][0]))


if __name__=='__main__':unittest.main()
