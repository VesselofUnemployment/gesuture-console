import _bootstrap
import queue,threading,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import numpy as np
import test_app_gestures as old
from session_pose import session_pose,thumb_three
from session_start import Process, already_running, start_session
from hand_tracking import decode_result,HandPresentation,Hand
from app_gestures import AppGesture
from app_launcher import AppLauncher, SESSION_KEYS


def upward(flags=(1,1,1,0,0)):
    world=old.points(flags)
    pixels=world[:,:2]*np.array((100,-100))+(320,300)
    return world,pixels


class PoseTests(unittest.TestCase):
    def test_folded_image_thumb_blocks_false_open_world_thumb_and_ring_depth_does_not_erase_normal_three(self):
        w,p=upward();closed_pixels=upward((0,1,1,0,0))[1]
        self.assertFalse(session_pose(w,closed_pixels))
        world,pixels=upward((0,1,1,1,0))
        world[15]=world[14]+(0,-.2,.05);world[16]=world[15]+(0,-.1,.01)
        self.assertFalse(thumb_three(world,pixels))

    def test_raised_curved_thumb_outside_palm_survives_uncertain_world_depth(self):
        w,p=upward();w[1:5]=old.points((0,1,1,0,0))[1:5]
        p[2]=p[5]+(-30,-5);p[3]=p[5]+(-20,-35);p[4]=p[5]+(-10,-65)
        self.assertTrue(session_pose(w,p))

    def test_thumb_two_fingers_up_is_session_numbers_and_sideways_are_excluded(self):
        w,p=upward();self.assertTrue(session_pose(w,p))
        self.assertTrue(session_pose(w,p*np.array((-1,1))))
        for flags in ((0,1,0,0,0),(0,1,1,0,0),(0,1,1,1,0),(0,1,1,1,1),
                      (1,1,1,1,1),(0,0,0,0,0),(1,1,0,0,1)):
            w,p=upward(flags);self.assertFalse(session_pose(w,p),flags)
        w,p=upward();self.assertFalse(session_pose(w,p[:,::-1]))
        self.assertFalse(session_pose(w,np.full((21,2),np.nan)))

    def test_decoder_reserves_thumb_three_and_retains_normal_three(self):
        for flags,session,count in (((1,1,1,0,0),True,None),((0,1,1,1,0),False,3),
                                   ((0,1,1,1,1),False,4),((1,1,1,1,1),False,5)):
            world,p=upward(flags)
            landmarks=[NS(x=x/640,y=y/480,z=0) for x,y in p]
            depth=[NS(x=x,y=y,z=z) for x,y,z in world]
            hand=decode_result(NS(hand_landmarks=[landmarks],hand_world_landmarks=[depth],gestures=[]),(480,640,3))[0]
            self.assertEqual(hand.session_start,session);self.assertEqual(hand.fingers,count)

    def test_pose_confirmation_and_one_action_until_neutral(self):
        w,p=upward();smooth=HandPresentation();hand=Hand(0,p,session_start=True)
        self.assertFalse(smooth.update([hand],(480,640,3),0)[0].session_start)
        self.assertTrue(smooth.update([hand],(480,640,3),.17)[0].session_start)
        self.assertEqual(smooth.update([],(480,640,3),.18),[])
        self.assertFalse(smooth.update([hand],(480,640,3),.2)[0].session_start)
        gate=AppGesture();old.TriggerTests.arm(self,gate)
        poses=[dict(number=1,gesture='',fingers=None,session_start=True)]
        actions=[gate.update(poses,'admin',.7+i*.1) for i in range(30)]
        self.assertEqual([a for a in actions if a],['session'])
        self.assertIsNone(gate.update(poses,'admin',4.,enabled=False))
        self.assertIsNone(gate.update(poses,None,4.1))


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.targets = {key: dict(kind='file', target=fr'C:\apps\tool{key}.exe')
                        for key in SESSION_KEYS}
        self.targets['3'] = dict(kind='app', target='Example.Tool_example!App')
        self.stop = threading.Event()
        self.opened = []
        self.pending = {}

    def run_batch(self, processes=(), opener=None, clock=lambda: 100.):
        return start_session(self.targets, self.stop, self.pending, snapshot=lambda: processes,
                             opener=opener or self.opened.append, clock=clock)

    def test_blank_release_has_no_launch_targets(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with patch('app_launcher.GESTURE_CONFIG', root / 'gesture_shortcuts.json'), \
                 patch('app_launcher.SESSION_CONFIG', root / 'session_shortcuts.json'):
                launcher = AppLauncher()
                self.assertTrue(all(not launcher.request(action)
                                    for action in (*SESSION_KEYS, 'fist', 'session')))
                self.assertEqual(launcher.bindings.count('Unassigned'), 7)
                self.assertEqual(list(root.iterdir()), [])
        from unittest.mock import Mock
        snapshot, opener = Mock(), Mock()
        self.assertFalse(start_session({}, self.stop, {}, snapshot=snapshot, opener=opener)[0])
        snapshot.assert_not_called()
        opener.assert_not_called()

    def test_configured_targets_only_in_order_with_duplicates_skipped(self):
        self.targets['2'] = self.targets['1']
        self.targets['fist'] = dict(kind='file', target=r'C:\apps\excluded.exe')
        del self.targets['4']
        ok, report = self.run_batch()
        self.assertTrue(ok)
        self.assertIn('Opening 3', report)
        self.assertEqual(self.opened, [self.targets[key] for key in ('1', '3', '5')])

    def test_running_executable_and_store_app_are_skipped(self):
        processes = [Process('tool1.exe', self.targets['1']['target']),
                     Process('packaged.exe', appid=self.targets['3']['target'])]
        ok, report = self.run_batch(processes)
        self.assertTrue(ok)
        self.assertIn('Active 2', report)
        self.assertEqual(self.opened, [self.targets[key] for key in ('2', '4', '5')])
        package = [Process('tool.exe', r'C:\Program Files\WindowsApps\Example.Tool_1.0_x64__example\tool.exe')]
        self.assertTrue(already_running(self.targets['3'], package))

    def test_pending_and_partial_failure(self):
        self.assertTrue(self.run_batch()[0])
        self.opened.clear()
        ok, report = self.run_batch(clock=lambda: 102.)
        self.assertTrue(ok)
        self.assertIn('Starting 5', report)
        self.assertEqual(self.opened, [])
        self.pending.clear()

        def partial(target):
            if target == self.targets['1']:
                raise OSError('test unavailable')
            self.opened.append(target)

        ok, report = self.run_batch(opener=partial)
        self.assertFalse(ok)
        self.assertIn('tool1: test unavailable', report)
        self.assertEqual(len(self.opened), 4)

    def test_cancel_and_uninspectable_executable(self):
        def first(target):
            self.opened.append(target)
            self.stop.set()

        self.assertFalse(self.run_batch(opener=first)[0])
        self.assertEqual(len(self.opened), 1)
        self.stop.clear()
        self.opened.clear()
        ok, _ = self.run_batch([Process('tool1.exe')])
        self.assertFalse(ok)
        self.assertNotIn(self.targets['1'], self.opened)

    def test_launcher_batch_is_async_and_keeps_busy_guard(self):
        entered, finish = threading.Event(), threading.Event()

        def runner(targets, stop, pending):
            entered.set()
            finish.wait(2)
            return False, 'Partial test result'

        launcher = AppLauncher(self.targets, session_runner=runner, session_targets=self.targets)
        try:
            self.assertTrue(launcher.request('session'))
            self.assertTrue(entered.wait(1))
            self.assertFalse(launcher.request('session'))
            self.assertFalse(launcher.request('1'))
            finish.set()
            launcher.thread.join(2)
            self.assertEqual(launcher.results.get_nowait(), ('session', False, 'Partial test result'))
        finally:
            finish.set()
            launcher.close()


if __name__=='__main__':unittest.main()
