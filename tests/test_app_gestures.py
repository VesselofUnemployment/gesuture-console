import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import patch
import json
import threading
import unittest
import numpy as np
from finger_count import number_pose,projected_number
from hand_tracking import decode_result,HandPresentation
from app_gestures import AppGesture,APP_NAMES
from app_launcher import load_targets,open_target,AppLauncher
import test_interactions


def points(flags):
    p=np.zeros((21,3),float)
    for extended,mcp,x,y in zip(flags[1:],(5,9,13,17),(-.3,-.1,.15,.4),(1,1.1,1.05,.9)):
        p[mcp]=(x,y,0);p[mcp+1]=(x,y+.3,0)
        p[mcp+2]=(x,y+.55,0) if extended else (x,y+.08,.06)
        p[mcp+3]=(x,y+.8,0) if extended else (x,y-.15,.04)
    p[1]=(-.25,.3,0)
    if flags[0]:p[2:5]=[(-.65,.5,0),(-.95,.7,0),(-1.25,.9,0)]
    else:p[2:5]=[(-.7,.55,0),(-.2,.7,0),(-.25,.95,0)]
    return p


class CountTests(unittest.TestCase):
    def test_folded_thumb_three_four_resolve_ambiguous_world_depth(self):
        for flags,count in (((0,1,1,1,0),3),((0,1,1,1,1),4)):
            visible=points(flags)[:,:2]
            world=points(flags)
            # Straight-looking 3D thumb with middling separation: original
            # classifier rejects the entire number despite clear image pose.
            start=np.array((-.25,.3,0));tip=np.array((-.7,1.1,0))
            world[1:5]=[start+(tip-start)*i/3 for i in range(4)]
            self.assertIsNone(number_pose(world))
            for rotation in (0.,.7,2.8):
                transform=np.array(((np.cos(rotation),-np.sin(rotation)),(np.sin(rotation),np.cos(rotation))))
                for mirror in (-1,1):
                    image=visible@transform*np.array((mirror,1))*150+np.array((320,240))
                    self.assertEqual(number_pose(world,image_points=image),count)
            # Pinch/camera wake/thumb gestures must not be reinterpreted.
            for gesture in ('ILoveYou','Thumb_Up','Thumb_Down'):
                self.assertIsNone(number_pose(world,gesture,visible))

    def test_three_four_with_bent_world_depth_and_clear_projected_fingers(self):
        for flags,count in (((0,1,1,1,0),3),((0,1,1,1,1),4)):
            world=points(flags);visible=world[:,:2].copy()
            world[7]=world[6]+np.array((0,.20,.168))
            world[8]=world[7]+np.array((0,.20,.168))
            self.assertIsNone(number_pose(world))
            self.assertEqual(number_pose(world,image_points=visible),count)
            # An Open_Palm classifier hint must not turn folded-thumb 4 into 5.
            self.assertEqual(number_pose(world,'Open_Palm',visible),count)

    def test_projection_does_not_turn_open_thumb_five_or_uncertain_hand_into_four(self):
        for flags,count in (((0,1,0,0,0),1),((0,1,1,0,0),2),((1,1,1,0,0),3),
                            ((1,1,1,1,1),5),((0,0,0,0,0),0)):
            p=points(flags)
            self.assertIsNone(projected_number(p[:,:2]))
            self.assertEqual(number_pose(p,image_points=p[:,:2]),count)
        valid=points((0,1,1,1,1))[:,:2]
        side=valid*np.array((.1,1.))
        for image in (np.zeros((21,2)),side,np.full((21,2),np.nan),np.zeros((20,2))):
            self.assertIsNone(projected_number(image))
        ambiguous=valid.copy();ambiguous[19]=ambiguous[18]+np.array((.16,.2))
        ambiguous[20]=ambiguous[19]+np.array((.16,.2))
        self.assertIsNone(projected_number(ambiguous))

    def test_decoder_uses_isotropic_mirrored_image_points_for_ambiguous_depth(self):
        flags=(0,1,1,1,1);visible=points(flags)[:,:2];world=points(flags)
        start=np.array((-.25,.3,0));tip=np.array((-.7,1.1,0))
        world[1:5]=[start+(tip-start)*i/3 for i in range(4)]
        for height,width in ((480,640),(720,1280)):
            pixels=visible*120+np.array((320,50))
            normalized=[NS(x=float(x/width),y=float(y/height),z=0.) for x,y in pixels]
            world_points=[NS(x=float(x),y=float(y),z=float(z)) for x,y,z in world]
            result=NS(hand_landmarks=[normalized],hand_world_landmarks=[world_points],gestures=[])
            self.assertEqual(decode_result(result,(height,width,3))[0].fingers,4)

    def test_all_numbers_fist_both_threes_and_rotated_hands(self):
        cases=[((0,0,0,0,0),0),((0,1,0,0,0),1),((0,1,1,0,0),2),
               ((0,1,1,1,0),3),((1,1,1,0,0),3),((0,1,1,1,1),4),((1,1,1,1,1),5)]
        rotations=[np.eye(3),np.linalg.qr(np.random.default_rng(2).normal(size=(3,3)))[0]]
        for flags,expected in cases:
            for rotation in rotations:
                self.assertEqual(number_pose(points(flags)@rotation),expected,(flags,rotation))
    def test_ily_thumb_gestures_invalid_and_non_number_poses_are_excluded(self):
        for gesture in ('ILoveYou','Thumb_Up','Thumb_Down'):
            self.assertIsNone(number_pose(points((1,1,1,1,1)),gesture))
        for p in (np.zeros((21,3)),np.zeros((20,3)),np.full((21,3),np.nan)):
            self.assertIsNone(number_pose(p,'Open_Palm'))
        self.assertIsNone(number_pose(points((1,1,0,0,1))))
    def test_world_landmarks_decode_and_short_count_stability(self):
        geometry=points((0,1,1,1,0))
        landmarks=[NS(x=float(x),y=float(y),z=float(z)) for x,y,z in geometry]
        result=NS(hand_landmarks=[landmarks],hand_world_landmarks=[landmarks],gestures=[])
        hands=decode_result(result,(480,640,3))
        self.assertEqual(hands[0].fingers,3)
        presentation=HandPresentation()
        self.assertIsNone(presentation.update(hands,(480,640,3),0)[0].fingers)
        self.assertEqual(presentation.update(hands,(480,640,3),.17)[0].fingers,3)
        self.assertIsNone(presentation.update(hands,(480,640,3),1)[0].fingers)


def pose(action):
    return [dict(number=1,gesture='Closed_Fist' if action=='fist' else '',fingers=0 if action=='fist' else int(action))]


class TriggerTests(unittest.TestCase):
    def arm(self,gate,start=0):
        for i in range(7):gate.update([],'admin',start+i*.1)
    def test_all_bindings_hold_once_and_require_neutral_release(self):
        for action in APP_NAMES:
            gate=AppGesture();self.arm(gate)
            fired=[gate.update(pose(action),'admin',.7+i*.1) for i in range(30)]
            self.assertEqual([x for x in fired if x],[action])
            self.assertFalse(any(gate.update(pose('2'),'admin',3.7+i*.1) for i in range(20)))
            self.arm(gate,5.7)
            self.assertEqual(sum(gate.update(pose(action),'admin',6.4+i*.1)==action for i in range(20)),1)
    def test_startup_held_pose_never_fires_until_released(self):
        gate=AppGesture()
        self.assertFalse(any(gate.update(pose('5'),'admin',i*.1) for i in range(30)))
    def test_neutral_release_does_not_add_another_second_of_wait(self):
        gate=AppGesture();self.arm(gate)
        self.assertEqual(sum(gate.update(pose('1'),'admin',.7+i*.1)=='1' for i in range(12)),1)
        for i in range(6):gate.update([],'admin',1.9+i*.1)
        self.assertEqual(sum(gate.update(pose('2'),'admin',2.5+i*.1)=='2' for i in range(12)),1)
    def test_wrong_identity_disabled_two_hands_loss_and_gaps_block(self):
        for mode in ('unknown','disabled','two','gap','identity'):
            gate=AppGesture();self.arm(gate)
            for i in range(30):
                hands=pose('3')*(2 if mode=='two' else 1)
                identity=None if mode=='unknown' else str(i%2) if mode=='identity' else 'admin'
                stamp=1+i*(.5 if mode=='gap' else .1)
                self.assertIsNone(gate.update(hands,identity,stamp,enabled=mode!='disabled'),mode)
    def test_ily_is_reserved_and_fist_needs_actual_classifier_confirmation(self):
        self.assertIsNone(AppGesture.action([dict(number=1,gesture='ILoveYou',fingers=3)]))
        self.assertIsNone(AppGesture.action([dict(number=1,gesture='',fingers=0)]))
        self.assertIsNone(AppGesture.action([dict(number=1,gesture='',fingers=True)]))


class LaunchTests(unittest.TestCase):
    def test_fixed_local_target_and_packaged_app_dispatch(self):
        with TemporaryDirectory() as folder,patch('app_launcher.os.startfile') as opened:
            executable=Path(folder)/'app.exe';executable.write_bytes(b'not executed')
            targets={key:dict(kind='file',target=str(executable)) for key in APP_NAMES}
            targets['5']=dict(kind='app',target='Example.Package_example!App')
            config=Path(folder)/'apps.json';config.write_text(json.dumps(targets))
            loaded=load_targets(config)
            for key,target in loaded.items():open_target(target)
            self.assertEqual(opened.call_count,6)
            self.assertIn('shell:AppsFolder\\Example.Package_example!App',[call.args[0] for call in opened.call_args_list])
            targets['5']['target']='app!id & cmd'
            config.write_text(json.dumps(targets))
            with self.assertRaises(ValueError):load_targets(config)
    def test_worker_failure_busy_and_close_do_not_block_camera(self):
        entered=threading.Event();released=threading.Event()
        def slow(target):entered.set();released.wait(2)
        launcher=AppLauncher({key:dict(kind='app',target='Example!App') for key in APP_NAMES})
        with patch('app_launcher.open_target',side_effect=slow):
            self.assertTrue(launcher.request('1'));self.assertTrue(entered.wait(1))
            self.assertFalse(launcher.request('2'))
            released.set();launcher.thread.join(2)
        self.assertEqual(launcher.results.get_nowait(),('1',True,''))
        with patch('app_launcher.open_target',side_effect=OSError('test unavailable')):
            launcher.request('fist');launcher.thread.join(2)
        self.assertEqual(launcher.results.get_nowait(),('fist',False,'test unavailable'))
        launcher.close();self.assertFalse(launcher.request('3'))


class CommandsTests(unittest.TestCase):
    setUp=test_interactions.InteractionCommandTests.setUp
    def test_help_apps_toggle_bindings_and_guard(self):
        self.assertIn('vision apps',self.engine.begin('help').text)
        self.assertIn('OFF',self.engine.begin('vision apps off').text)
        self.assertFalse(self.engine.vision.snapshot()['apps_enabled'])
        self.assertIn('5 Unassigned',self.engine.begin('vision apps on').text)
        self.assertIn('APP SHORTCUTS',self.engine.begin('vision status').text)
        for text in ('vision apps maybe','vision apps on extra','sudo vision apps off'):
            with self.assertRaises(ValueError):self.engine.begin(text)
        self.allowed=False
        with self.assertRaises(ValueError):self.engine.begin('vision apps off')


if __name__=='__main__':unittest.main()
