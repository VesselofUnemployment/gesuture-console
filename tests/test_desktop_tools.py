import _bootstrap
import math
import unittest
import time
import test_desktop_control as base
from desktop_control import COMMANDS, DesktopControl, Rect, sector
from desktop_tools import MENUS
from types import SimpleNamespace


class ToolTests(unittest.TestCase):
    hand=base.PolicyTests.hand
    hold=base.PolicyTests.hold
    wheel=base.PolicyTests.wheel
    setUp=base.PolicyTests.setUp

    def select(self,index):
        count=len(self.policy.view()['commands'])
        a=-math.pi/2+index*math.tau/count
        point=(700+math.cos(a)*150,620+math.sin(a)*150)
        self.hold(self.hand(point),.3)
        return self.hold(self.hand(point,pinch=.1),.2)

    def test_submenu_selection_is_once_per_pinch_and_never_legacy_apps(self):
        for mode,commands in MENUS.items():
            for index,(action,label) in enumerate(commands):
                self.policy.set_enabled(True);self.policy.mode=mode
                self.assertEqual(self.select(index),[action])
                self.assertEqual(self.hold(self.hand(pinch=.1),.3),[])
        self.assertEqual(len(COMMANDS),8)
        self.assertFalse(set(dict(COMMANDS))&{'1','2','3','4','5','fist'})

    def test_sectors_match_every_submenu_size_and_boundary(self):
        for count in (4,5,8):
            for index in range(count):
                angle=index*math.tau/count-math.pi/2
                point=(700+math.cos(angle)*150,620+math.sin(angle)*150)
                self.assertEqual(sector(point,(700,620),200,count),index)

    def test_capture_needs_drag_release_and_second_deliberate_pinch(self):
        self.policy.mode='capture'
        self.hold(self.hand((500,400)));self.hold(self.hand((500,400),pinch=.1))
        self.hold(self.hand((900,800),pinch=.1))
        self.assertIsNotNone(self.policy.tools.region)
        self.assertIsNone(self.policy.tools.capture_request)
        self.hold(self.hand((900,800)))
        self.assertTrue(self.policy.tools.confirm)
        selected=self.policy.tools.region
        self.assertEqual(self.hold(self.hand((900,800),pinch=.1)),['capture-save'])
        self.assertEqual(self.policy.tools.capture_request,selected)
        self.assertEqual(self.hold(self.hand((900,800),pinch=.1),1.),[])

    def test_capture_small_region_palm_cancel_and_loss_cannot_save_old_data(self):
        self.policy.mode='capture'
        self.hold(self.hand());self.hold(self.hand(pinch=.1));self.hold(self.hand())
        self.assertFalse(self.policy.tools.confirm)
        self.hold(self.hand((500,400),pinch=.1));self.hold(self.hand((900,800),pinch=.1));self.hold(self.hand((900,800)))
        self.assertTrue(self.policy.tools.confirm)
        self.hold(self.hand(opened=True));self.assertIsNone(self.policy.tools.region)
        self.hold(self.hand((500,400),pinch=.1));self.hold(self.hand((900,800),pinch=.1));self.hold(self.hand((900,800)))
        self.now+=.1;self.policy.update([],self.now)
        self.assertEqual(self.policy.mode,'capture')
        self.assertIsNone(self.policy.tools.region)
        self.assertEqual(self.hold(self.hand(pinch=.1)),[])
        self.assertIsNone(self.policy.tools.capture_request)

    def test_drawing_breaks_stroke_on_loss_and_requires_fresh_release(self):
        self.policy.mode='draw'
        self.hold(self.hand((500,400)));self.hold(self.hand((500,400),pinch=.1))
        self.hold(self.hand((700,500),pinch=.1))
        before=[list(s) for s in self.policy.tools.strokes]
        self.now+=.1;self.policy.update([],self.now)
        self.hold(self.hand((1100,900),pinch=.1))
        self.assertEqual(self.policy.tools.strokes,before)
        self.hold(self.hand((1100,900)));self.hold(self.hand((1100,900),pinch=.1))
        self.assertEqual(len(self.policy.tools.strokes),2)
        self.assertGreater(self.policy.tools.strokes[1][0][0],1000)
        self.hold(self.hand(opened=True),.6)
        self.assertEqual(self.policy.mode,'draw-menu')
        self.assertEqual(self.select(1),['draw-undo'])
        self.assertEqual(len(self.policy.tools.strokes),1)
        self.hold(self.hand(opened=True),.6)
        self.assertEqual(self.select(2),['draw-clear'])
        self.assertEqual(self.policy.tools.strokes,[])

    def test_fist_returns_mouse_without_task_manager_and_permission_clears_tools(self):
        for mode in ('workspace','media','capture','draw','draw-menu'):
            self.policy.set_enabled(True);self.policy.mode=mode
            self.policy.tools.hud=True;self.policy.tools.strokes=[[(500,400),(600,500)]]
            self.assertEqual(self.hold(self.hand(fist=True,pinch=.1),.7),[])
            self.assertEqual(self.policy.mode,'ready')
            self.assertTrue(self.policy.tools.hud)
            self.policy.update([self.hand()],self.now+.1,authorized=False)
            self.assertFalse(self.policy.tools.hud);self.assertEqual(self.policy.tools.strokes,[])

    def test_short_loss_preserves_menu_but_three_seconds_resets_tools(self):
        for mode in MENUS:
            self.policy.set_enabled(True);self.policy.mode=mode
            self.hold(self.hand());self.policy.update([],self.now+2.9)
            self.assertEqual(self.policy.mode,mode)
            self.policy.update([],self.now+3.)
            self.assertEqual(self.policy.mode,'ready')

    def test_drawing_memory_is_bounded_and_undo_clear_recover(self):
        tools=self.policy.tools;tools.MAX_POINTS=4;tools.MAX_STROKES=2
        hand={'open':False}
        tools.update('draw',(0,0),'down',True,hand,1.,1.)
        for i in range(1,10):tools.update('draw',(10*i,0),None,True,hand,1.+i/20,1.)
        self.assertEqual(sum(map(len,tools.strokes)),4)
        tools.update('draw',(200,0),'down',True,hand,2.,1.)
        self.assertEqual(sum(map(len,tools.strokes)),4)
        tools.clear();self.assertEqual(tools.strokes,[])


class Services:
    def __init__(self):self.hud=False;self.captures=[];self.cancelled=0
    def set_hud(self,value):self.hud=value
    def capture(self,rect):self.captures.append(rect);return True
    def metrics(self):return {'cpu':5,'gpu':3,'ram':50}
    def result(self):return None
    def cancel_capture(self):self.cancelled+=1
    def deactivate(self):self.hud=False;self.cancel_capture()
    def close(self):self.deactivate()


class ToolAdapterTests(unittest.TestCase):
    frames=base.AdapterTests.frames
    def setUp(self):
        self.backend=base.Backend();self.launches=[];self.surface=base.Overlay();self.now=10.
        self.services=Services()
        self.control=DesktopControl(SimpleNamespace(request=self.launches.append),
            backend_factory=lambda:self.backend,overlay_factory=lambda:self.surface,
            services_factory=lambda:self.services)
        self.control.set_enabled(True)

    def test_media_uses_shortcut_without_click_launch_or_repeat(self):
        self.control.policy.mode='media'
        self.frames(point=(700,470));self.frames(point=(700,470),pinch=.1,seconds=1.)
        self.assertEqual(self.backend.commands,['media-play'])
        self.assertEqual(self.backend.clicks,[]);self.assertEqual(self.backend.cursor_moves,[])
        self.assertEqual(self.launches,[])

    def test_hud_and_ink_cleared_on_off_and_capture_cancelled_on_permission_loss(self):
        self.control.policy.tools.hud=True;self.frames()
        self.assertTrue(self.services.hud)
        self.control.capture_pending=(20.,Rect(1,2,100,200))
        self.control.update([],self.now+.05,False)
        self.assertFalse(self.services.hud)
        self.assertIsNone(self.control.capture_pending)
        self.assertEqual(self.services.captures,[])

    def test_capture_waits_for_overlay_hide_and_rejects_missing_frame(self):
        self.control.policy.mode='capture';self.frames()
        self.control.capture_pending=(self.now+.2,Rect(1,2,100,200))
        self.control.present();self.assertFalse(self.surface.visible)
        self.control.poll(self.now+.1);self.assertEqual(self.services.captures,[])
        self.control.policy.last=self.now+.2
        self.control.poll(self.now+.2);self.assertEqual(len(self.services.captures),1)
        self.control.capture_pending=(self.now+.5,Rect(1,2,100,200))
        self.control.poll(self.now+.5)
        self.assertEqual(len(self.services.captures),1)

    def test_workspace_sends_once_then_recreates_overlay_on_new_desktop(self):
        created=[]
        def factory():
            surface=base.Overlay();created.append(surface);return surface
        self.control.overlay_factory=factory
        self.control.policy.mode='workspace'
        self.frames(point=(700,470));self.frames(point=(700,470),pinch=.1,seconds=.2)
        self.assertEqual(self.backend.commands,['desk-left'])
        self.assertIsNotNone(self.control.rebuild_at)
        due=self.control.rebuild_at;self.control.policy.last=due
        self.control.poll(due-.1);self.assertEqual(created,[])
        self.control.poll(due)
        self.assertEqual(len(created),1);self.assertTrue(self.surface.closed)
        self.assertIsNone(self.control.rebuild_at)
        self.assertEqual(self.control.policy.mode,'workspace')
        self.assertTrue(created[0].visible)
        self.assertEqual(self.launches,[]);self.assertEqual(self.backend.clicks,[])


if __name__=='__main__':unittest.main()
