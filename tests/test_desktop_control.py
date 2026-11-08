import _bootstrap
import math
import unittest
from types import SimpleNamespace
import numpy as np
from desktop_control import Rect, ControlPolicy, Pinch, DesktopControl, hand_input, screen_point, sector, filtered_point, steady_sector, proportional_size


class Backend:
    area=Rect(-300,20,1700,1220)
    center=(700,620)
    radius=200
    scale=1.
    def __init__(self):
        self.moves=[];self.available=True;self.escape=False
        self.cursor_moves=[];self.clicks=[];self.releases=0;self.mouse_available=True
        self.commands=[]
        self.limits=((200,120),(2000,1200));self.limit_queries=0
        self.window=SimpleNamespace(hwnd=9,pid=21,title='Test window',rect=Rect(500,400,1100,800),resizable=True)
    def command(self,action):self.commands.append(action);return True
    def pick(self,point):return self.window if self.available else None
    def valid(self,target):return self.available
    def grab(self,target,point):return target if self.available else None
    def place(self,target,rectangle):self.moves.append(rectangle);return self.available
    def resize_limits(self,target):
        self.limit_queries+=1;return self.limits
    def escape_pressed(self):return self.escape
    def move_cursor(self,point):
        self.cursor_moves.append(point);return self.mouse_available
    def click_left(self,point):self.clicks.append(point);return self.mouse_available
    def release_mouse(self):self.releases+=1;return True


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.backend=Backend();self.policy=ControlPolicy(self.backend);self.policy.set_enabled(True)
        self.now=10.
    def hand(self,point=None,pinch=2.,opened=False,number=1,fist=False):
        point=point or self.backend.center
        a=self.backend.area
        position=(.16+.68*(point[0]-a.left)/(a.width-1),.14+.72*(point[1]-a.top)/(a.height-1))
        return dict(number=number,position=position,resize_position=(position[0],position[1]*.75),
                    pinch=pinch,open=opened,fist=fist)
    def hold(self,hand,seconds=.3):
        actions=[]
        for _ in range(round(seconds/.05)):
            self.now+=.05
            result=self.policy.update([hand],self.now)
            if result:actions.append(result)
        return actions
    def wheel(self):
        self.hold(self.hand(opened=True),.6)
        self.assertEqual(self.policy.mode,'wheel')
    def choose(self,index):
        angle=-math.pi/2+index*math.pi/4
        point=(700+math.cos(angle)*150,620+math.sin(angle)*150)
        self.hold(self.hand(point),.4)
        return self.hold(self.hand(point,pinch=.1),.3)
    def move(self):
        self.wheel();self.assertEqual(self.choose(5),['move'])
        self.hold(self.hand(),.3)
        self.hold(self.hand(pinch=.1),.2)
        self.assertIsNotNone(self.policy.drag)

    def test_all_wheel_actions_once_per_pinch(self):
        for index,action in enumerate(('workspace','media','capture','hud','draw','move','back','off')):
            self.policy.set_enabled(True);self.wheel()
            self.assertEqual(self.choose(index),[action])
            self.assertEqual(self.hold(self.hand(pinch=.1),1.),[])

    def test_no_action_without_open_palm_or_on_initial_held_pinch(self):
        self.hold(self.hand(pinch=.1),1.)
        self.assertEqual(self.policy.mode,'ready')
        self.assertIsNone(self.policy.drag)
        self.policy.mode='move';self.hold(self.hand(pinch=.1),.3)
        self.assertIsNone(self.policy.drag)

    def test_drag_tracks_one_target_and_snaps_only_on_release(self):
        self.move();target=self.policy.drag
        self.hold(self.hand((-300,620),pinch=.1),.6)
        self.assertEqual(self.policy.drag,target)
        self.assertIsNotNone(self.policy.preview)
        self.assertNotEqual(self.backend.moves[-1],Rect(-300,20,700,1220))
        self.hold(self.hand((-300,620)),.2)
        self.assertIsNone(self.policy.drag)
        self.assertEqual(self.backend.moves[-1],Rect(-300,20,700,1220))

    def test_loss_multiple_hands_other_identity_and_stale_cancel_without_snap(self):
        for kind in ('missing','identity','stale','hand-switch'):
            self.policy.set_enabled(True);self.move();before=len(self.backend.moves)
            if kind=='missing':self.policy.update([],self.now+.1)
            elif kind=='multiple':self.policy.update([self.hand(),self.hand(number=2)],self.now+.1)
            elif kind=='identity':self.policy.update([self.hand()],self.now+.1,authorized=False)
            elif kind=='stale':self.now+=1.;self.policy.update([self.hand(pinch=.1)],self.now)
            else:self.policy.update([self.hand(number=2,pinch=.1)],self.now+.1)
            self.assertIsNone(self.policy.drag,kind)
            self.assertEqual(len(self.backend.moves),before,kind)
            self.hold(self.hand(pinch=.1),.4)
            self.assertIsNone(self.policy.drag)

    def test_destroyed_target_does_not_get_replaced_by_other_window(self):
        self.move();self.backend.available=False
        before=len(self.backend.moves);self.hold(self.hand(pinch=.1),.3)
        self.assertIsNone(self.policy.drag)
        self.assertEqual(len(self.backend.moves),before)

    def test_pinch_hysteresis_and_release_before_rearming(self):
        pinch=Pinch()
        self.assertIsNone(pinch.update(.1,0.))
        self.assertIsNone(pinch.update(.1,.1))
        pinch.update(.8,.2);self.assertEqual(pinch.update(.8,.31),'up')
        pinch.update(.1,.4);self.assertEqual(pinch.update(.1,.51),'down')
        self.assertIsNone(pinch.update(.38,.62));self.assertTrue(pinch.down)
        pinch.update(.6,.7);self.assertEqual(pinch.update(.6,.81),'up')

    def test_mirrored_landmarks_drive_real_pinch_and_invalid_points_rejected(self):
        points=np.zeros((21,2),np.float32);points[5]=(200,150);points[17]=(250,150)
        points[9]=(225,150);points[0]=(225,200);points[8]=(210,100);points[4]=(215,100)
        hand=SimpleNamespace(points=points,number=1,gesture='Open_Palm')
        result=hand_input(hand,(480,640,3))
        self.assertAlmostEqual(result['pinch'],.1)
        self.assertTrue(result['open'])
        self.assertLess(result['position'][0],.5)
        points[8]=(float('nan'),0)
        self.assertIsNone(hand_input(hand,(480,640,3)))

    def test_coordinate_mapping_respects_negative_origin_and_taskbar_work_area(self):
        self.assertEqual(screen_point((0,0),self.backend.area),(-300,20))
        self.assertEqual(screen_point((1,1),self.backend.area),(1699,1219))
        self.assertIsNone(sector((700,620),(700,620),200))

    def test_short_hand_dropouts_preserve_wheel_without_replaying_entrance(self):
        self.wheel();self.choose_point=(700,470)
        self.hold(self.hand(self.choose_point),.3)
        original=self.policy.point
        self.now+=.1;self.policy.update([],self.now)
        self.assertEqual(self.policy.mode,'wheel');self.assertEqual(self.policy.point,original)
        self.assertFalse(self.policy.tracking)
        self.now+=.3;self.policy.update([],self.now)
        self.assertEqual(self.policy.mode,'wheel')
        self.assertEqual(self.hold(self.hand(self.choose_point,pinch=.1),.2),[])
        self.assertEqual(self.policy.mode,'wheel')
        self.hold(self.hand(self.choose_point),.3)
        self.assertEqual(self.hold(self.hand(self.choose_point,pinch=.1),.2),['workspace'])
        self.policy.set_enabled(True);self.wheel()
        self.policy.update([],self.now+2.99)
        self.assertEqual(self.policy.mode,'wheel')
        self.policy.update([],self.now+3.)
        self.assertEqual(self.policy.mode,'ready');self.assertIsNone(self.policy.point)

    def test_no_frame_watchdog_cancels_drag_before_visual_grace_ends(self):
        self.move();count=len(self.backend.moves)
        self.policy.expire(self.now+.25)
        self.assertIsNone(self.policy.drag);self.assertFalse(self.policy.tracking)
        self.assertEqual(len(self.backend.moves),count)
        self.assertEqual(self.policy.mode,'move')

    def test_pinch_highlight_matches_visible_cursor_instead_of_hidden_lock(self):
        self.wheel();self.hold(self.hand((700,470)),.3)
        self.assertEqual(self.policy.hover,0)
        # Fingers close with the fingertip moving into the adjacent slice.
        self.hold(self.hand((850,620),pinch=.65),.15)
        self.assertEqual(self.policy.hover,2)
        self.assertEqual(self.hold(self.hand((850,620),pinch=.1),.2),['capture'])

    def test_slice_matches_geometric_boundary_and_radial_limits(self):
        def at(angle):
            a=math.radians(angle)-math.pi/2
            return (700+150*math.cos(a),620+150*math.sin(a))
        for angle in (21,25,20,26,24):
            self.assertEqual(steady_sector(at(angle),(700,620),200,0),sector(at(angle),(700,620),200))
        self.assertEqual(steady_sector(at(34),(700,620),200,0),1)
        self.assertIsNone(steady_sector((700,620),(700,620),200,0))

    def both(self,left=(500,620),right=(900,620),pinch=2.,seconds=.25,reverse=False):
        for _ in range(round(seconds/.05)):
            self.now+=.05
            pair=[self.hand(left,pinch,number=1),self.hand(right,pinch,number=2)]
            self.policy.update(pair[::-1] if reverse else pair,self.now)

    def test_open_palm_releases_window_but_does_not_exit_window_mode(self):
        self.move();self.hold(self.hand(opened=True),1.)
        self.assertIsNone(self.policy.drag);self.assertEqual(self.policy.mode,'move')

    def test_deliberate_fist_exits_move_without_launching_task_manager(self):
        self.move();count=len(self.backend.moves)
        self.assertEqual(self.hold(self.hand(pinch=.1,fist=True),.3),[])
        self.assertEqual(self.policy.mode,'move');self.assertIsNone(self.policy.drag)
        self.assertEqual(len(self.backend.moves),count)
        self.assertEqual(self.hold(self.hand(pinch=.1,fist=True),.4),[])
        self.assertEqual(self.policy.mode,'ready')

    def test_two_hands_resize_preserves_ratio_and_releases_without_jump(self):
        self.policy.mode='move'
        self.both();self.both(pinch=.1)
        self.assertIsNotNone(self.policy.resize)
        original=self.policy.bounds
        self.both(left=(400,620),right=(1000,620),pinch=.1,reverse=True)
        larger=self.backend.moves[-1]
        self.assertGreater(larger.width,original.width)
        self.assertAlmostEqual(larger.width/larger.height,original.width/original.height,places=2)
        self.both(left=(600,620),right=(800,620),pinch=.1)
        self.assertLess(self.backend.moves[-1].width,original.width)
        count=len(self.backend.moves)
        self.both(left=(600,620),right=(800,620),pinch=2.)
        self.assertIsNone(self.policy.resize);self.assertIsNone(self.policy.drag)
        self.assertEqual(self.policy.mode,'move');self.assertEqual(len(self.backend.moves),count)

    def test_second_hand_pauses_drag_then_can_start_resize(self):
        self.move();count=len(self.backend.moves)
        self.both(pinch=2.)
        self.assertEqual(self.policy.mode,'move');self.assertEqual(len(self.backend.moves),count)
        self.both(pinch=.1);self.assertIsNotNone(self.policy.resize)

    def test_resize_hand_loss_needs_new_pinch_and_fixed_windows_cannot_resize(self):
        self.policy.mode='move';self.both();self.both(pinch=.1)
        count=len(self.backend.moves)
        self.hold(self.hand(pinch=.1),.2)
        self.assertIsNone(self.policy.resize);self.assertIsNone(self.policy.drag)
        self.assertEqual(self.policy.mode,'move');self.assertEqual(len(self.backend.moves),count)
        self.backend.window.resizable=False
        self.both();self.both(pinch=.1)
        self.assertIsNone(self.policy.resize);self.assertEqual(len(self.backend.moves),count)

    def test_move_survives_two_second_gap_but_requires_fresh_release(self):
        self.move();count=len(self.backend.moves)
        self.policy.update([],self.now+2.)
        self.assertEqual(self.policy.mode,'move');self.assertFalse(self.policy.tracking)
        self.now+=2.;self.hold(self.hand(pinch=.1),.2)
        self.assertIsNone(self.policy.drag);self.assertEqual(len(self.backend.moves),count)
        self.hold(self.hand(),.2);self.hold(self.hand(pinch=.1),.2)
        self.assertIsNotNone(self.policy.drag)

    def test_resize_clamped_to_work_area_and_pinch_initially_closed_does_not_arm(self):
        self.policy.mode='move';self.both(pinch=.1)
        self.assertIsNone(self.policy.resize)
        self.both();self.both(pinch=.1)
        self.both(left=(-300,20),right=(1699,1219),pinch=.1)
        rect=self.backend.moves[-1];area=self.backend.area
        self.assertGreaterEqual(rect.left,area.left);self.assertLessEqual(rect.right,area.right)
        self.assertGreaterEqual(rect.top,area.top);self.assertLessEqual(rect.bottom,area.bottom)

    def test_asymmetric_app_minimum_keeps_original_ratio_and_queries_once(self):
        # App needs 350 px height. Clamping only height formerly made it tall.
        self.backend.limits=((220,350),(2000,1200))
        self.policy.mode='move';self.both();self.both(pinch=.1)
        self.both(left=(695,620),right=(705,620),pinch=.1)
        rect=self.backend.moves[-1]
        self.assertGreaterEqual(rect.height,350)
        self.assertAlmostEqual(rect.width/rect.height,1.5,places=2)
        self.assertEqual(self.backend.limit_queries,1)
        for _ in range(3):self.both(left=(695,620),right=(705,620),pinch=.1)
        self.assertEqual(self.backend.limit_queries,1)

    def test_gain_depends_on_camera_movement_not_initial_hand_gap(self):
        scales=[]
        for span in (.10,.60):
            self.policy.resize=(self.backend.window.rect,span,(200,120),(2000,1200))
            smaller=self.policy.resize_rectangle(span-.03)
            larger=self.policy.resize_rectangle(span+.03)
            self.assertGreater(smaller.width/600,.95)
            self.assertLess(larger.width/600,1.05)
            scales.append((smaller.width,larger.width))
        self.assertEqual(scales[0],scales[1])
        # About 3 camera pixels of jitter do not change the initial size.
        self.assertEqual(self.policy.resize_rectangle(.604),self.backend.window.rect)

    def test_resize_uses_palm_camera_position_even_when_cursor_hits_screen_edge(self):
        self.policy.mode='move'
        def pair(span,pinch,position):
            for _ in range(6):
                self.now+=.05
                hands=[self.hand(pinch=pinch,number=n) for n in (1,2)]
                for n,hand in enumerate(hands):
                    hand['position']=position
                    hand['resize_position']=(.5+(-1 if n==0 else 1)*span/2,.3)
                self.policy.update(hands,self.now)
        pair(.3,2.,(1.,1.));pair(.3,.1,(1.,1.))
        self.assertIsNotNone(self.policy.resize)
        original=self.policy.bounds
        # Cursor is clipped and moves abruptly. Stationary palms keep size.
        pair(.3,.1,(0.,0.));self.assertEqual(self.policy.bounds,original)
        pair(.5,.1,(0.,0.));self.assertGreater(self.policy.bounds.width,original.width)

    def test_uniform_size_constraints_include_restore_and_incompatible_limits(self):
        original=Rect(0,0,2200,1100)
        self.assertEqual(proportional_size(original,1.,(200,120),(1920,1080)),(1920,960))
        self.assertEqual(proportional_size(original,.1,(400,500),(1920,1080)),(1000,500))
        self.assertIsNone(proportional_size(original,1.,(200,1200),(1920,1080)))

    def test_invalid_limit_combination_releases_without_resize(self):
        self.backend.limits=((200,1500),(2000,1200))
        self.policy.mode='move';self.both();self.both(pinch=.1)
        self.assertIsNone(self.policy.resize);self.assertIsNone(self.policy.drag)
        self.assertEqual(self.backend.moves,[])
        self.assertEqual(self.policy.mode,'move')

    def test_too_close_hands_do_not_restore_or_grab_a_window(self):
        def unexpected_grab(*args):raise AssertionError('Window restored before hands were separated')
        self.backend.grab=unexpected_grab
        self.policy.mode='move'
        self.both(left=(690,620),right=(710,620))
        self.both(left=(690,620),right=(710,620),pinch=.1)
        self.assertIsNone(self.policy.resize)
        self.assertEqual(self.backend.moves,[])

    def test_palm_anchor_does_not_follow_pinch_fingertip_or_camera_aspect(self):
        points=np.zeros((21,2),np.float32)
        points[0]=(300,260);points[5]=(270,200);points[9]=(300,190)
        points[13]=(320,200);points[17]=(340,210);points[4]=(300,150);points[8]=(305,150)
        hand=SimpleNamespace(points=points,number=1,gesture='')
        before=hand_input(hand,(480,640,3))
        points[8]=(302,153)
        after=hand_input(hand,(360,640,3))
        self.assertEqual(before['resize_position'],after['resize_position'])
        self.assertNotEqual(before['position'],after['position'])

    def test_filter_reduces_resting_jitter_without_slowing_fast_movement(self):
        raw=[(700+8*math.sin(i*2.3),620+6*math.sin(i*1.7)) for i in range(100)]
        point=(700,620);filtered=[]
        for value in raw:
            point=filtered_point(point,value,1/30);filtered.append(point)
        raw_error=np.sqrt(np.mean(np.square(np.asarray(raw)-(700,620))))
        filtered_error=np.sqrt(np.mean(np.square(np.asarray(filtered)-(700,620))))
        self.assertLess(filtered_error,raw_error*.6,(raw_error,filtered_error))
        point=filtered_point((700,620),(1000,620),1/30)
        self.assertLess(math.dist(point,(1000,620)),12.)

    def test_control_uses_raw_points_instead_of_smoothing_a_second_time(self):
        raw=np.zeros((21,2),np.float32);raw[5]=(200,150);raw[17]=(250,150)
        raw[9]=(225,150);raw[0]=(225,200);raw[8]=(210,100);raw[4]=(215,100)
        smoothed=raw.copy();smoothed[:,0]+=50
        hand=SimpleNamespace(points=smoothed,raw_points=raw,number=1,gesture='')
        result=hand_input(hand,(480,640,3))
        self.assertAlmostEqual(result['position'][0],210/639)


class Overlay:
    def __init__(self):self.visible=False;self.closed=False
    def show(self,*args):self.visible=True
    def hide(self):self.visible=False
    def close(self):self.closed=True


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.backend=Backend();self.launches=[];self.surface=Overlay();self.now=10.
        self.control=DesktopControl(SimpleNamespace(request=self.launches.append),
            backend_factory=lambda:self.backend,overlay_factory=lambda:self.surface)
        self.control.set_enabled(True)

    def frames(self,point=(700,620),pinch=2.,opened=False,fist=False,seconds=.3):
        area=self.backend.area
        hand=dict(number=1,position=(.16+.68*(point[0]-area.left)/(area.width-1),
                  .14+.72*(point[1]-area.top)/(area.height-1)),pinch=pinch,open=opened,fist=fist)
        for _ in range(round(seconds/.05)):
            self.now+=.05;self.control.update([hand],self.now,True)

    def test_mouse_click_once_at_visible_aim_even_if_finger_moves_on_close(self):
        self.frames();aim=self.control.policy.point
        self.frames(point=(780,680),pinch=.1,seconds=1.)
        self.assertEqual(self.backend.clicks,[aim])
        self.assertEqual(self.backend.cursor_moves[-1],self.control.policy.point)
        self.assertEqual(self.control.policy.point,aim)
        self.frames(point=(900,700));self.frames(point=(900,700),pinch=.1)
        self.assertEqual(len(self.backend.clicks),2)
        self.assertGreater(self.backend.clicks[-1][0],aim[0]+150)
        self.assertEqual(self.launches,[])

    def test_pinch_held_at_entry_or_after_loss_cannot_click_without_release(self):
        self.frames(pinch=.1);self.assertEqual(self.backend.clicks,[])
        self.frames();self.frames(pinch=.1);self.assertEqual(len(self.backend.clicks),1)
        count=len(self.backend.cursor_moves)
        self.now+=.1;self.control.update([],self.now,True)
        self.assertEqual(len(self.backend.cursor_moves),count)
        self.frames(pinch=.1);self.assertEqual(len(self.backend.clicks),1)
        self.frames();self.frames(pinch=.1);self.assertEqual(len(self.backend.clicks),2)

    def test_wheel_and_window_pinch_do_not_send_mouse_input(self):
        self.frames(opened=True,seconds=.6)
        self.assertEqual(self.control.policy.mode,'wheel')
        count=len(self.backend.cursor_moves)
        self.frames(point=(700,470));self.frames(point=(700,470),pinch=.1,seconds=.1)
        self.assertEqual(self.launches,[]);self.assertEqual(self.control.policy.mode,'workspace');self.assertEqual(self.backend.clicks,[])
        self.assertEqual(len(self.backend.cursor_moves),count)
        self.frames(pinch=.1);self.assertEqual(self.backend.clicks,[])
        self.control.policy.mode='move'
        count=len(self.backend.cursor_moves)
        self.frames();self.frames(pinch=.1);self.frames(point=(950,700),pinch=.1)
        self.assertTrue(self.backend.moves)
        self.assertEqual(len(self.backend.cursor_moves),count)
        self.assertEqual(self.backend.clicks,[])

    def test_fist_is_not_a_mouse_click_and_permission_off_stops_input(self):
        self.frames();self.frames(pinch=.1,fist=True)
        self.assertEqual(self.backend.clicks,[])
        count=len(self.backend.cursor_moves)
        self.control.update([dict(number=1,position=(.2,.2),pinch=.1,open=False)],self.now+.05,False)
        self.assertFalse(self.control.enabled)
        self.assertEqual(len(self.backend.cursor_moves),count)
        self.assertGreater(self.backend.releases,0)

    def test_duplicate_and_stale_packets_never_replay_a_mouse_move_or_click(self):
        self.frames();count=len(self.backend.cursor_moves)
        hand=dict(number=1,position=(.8,.8),pinch=.1,open=False)
        self.control.update([hand],self.now,True)
        self.control.update([hand],self.now-.1,True)
        self.assertEqual(len(self.backend.cursor_moves),count)
        self.control.update([hand],self.now+.1,True,fresh=False)
        self.assertEqual(len(self.backend.cursor_moves),count);self.assertEqual(self.backend.clicks,[])

    def test_mouse_api_failure_disables_control_instead_of_injecting_elsewhere(self):
        self.backend.mouse_available=False
        self.frames()
        self.assertFalse(self.control.enabled);self.assertTrue(self.control.error)
        self.assertEqual(self.backend.clicks,[])

    def test_mouse_can_reach_taskbar_while_window_controls_keep_work_area(self):
        self.backend.mouse_area=Rect(-300,20,1700,1300)
        self.frames(point=(700,1219))
        self.assertGreater(self.backend.cursor_moves[-1][1],self.backend.area.bottom)

    def test_off_is_lazy_and_watchdog_escape_permission_cancel(self):
        calls=[];backend=Backend();overlay=Overlay()
        control=DesktopControl(SimpleNamespace(request=calls.append),
            backend_factory=lambda:backend,overlay_factory=lambda:overlay)
        control.update([],10.,False);self.assertIsNone(control.backend)
        self.assertTrue(control.set_enabled(True))
        control.policy.last=10.;control.policy.drag=backend.window
        control.poll(10.7);self.assertIsNone(control.policy.drag)
        control.update([],11.,False);self.assertFalse(control.enabled)
        control.set_enabled(True);backend.escape=True;control.poll(12.)
        self.assertFalse(control.enabled)
        control.close();self.assertTrue(overlay.closed)
        self.assertEqual(calls,[])


if __name__=='__main__':unittest.main()
