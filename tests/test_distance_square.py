"""Distance bands must reject jitter and briefly wrong face measurements."""
import _bootstrap  # Resolve application imports from any working directory.
import unittest
import numpy as np
from vision_hud import BoxSmoother, DistanceSizer, square_bounds, square_levels, BLUE

SHAPE = (480, 640, 3)


def face(height, width=None, center=(320,240)):
    width = height*.72 if width is None else width
    return np.array([center[0]-width/2,center[1]-height/2,width,height],np.float32)


class DistanceSquareTests(unittest.TestCase):
    def test_fast_sideways_motion_does_not_trail_behind_history(self):
        smoother=BoxSmoother()
        for i in range(6):
            smoother.update([(face(246,center=(250,240)),'Admin',BLUE,.8)],SHAPE,i/30)
        shown=None
        for i in (6,7,8):
            shown=smoother.update([(face(246,center=(330,240)),'Admin',BLUE,.8)],SHAPE,i/30)[0][0]
        self.assertLess(abs(float(shown[0]+shown[2]/2)-330),12)

    def test_approaching_across_bands_does_not_restart_confirmation(self):
        sizer=DistanceSizer()
        sizer.update(face(246),SHAPE,0)
        for i,height in enumerate((260,285,310,340,370),1):
            sizer.update(face(height),SHAPE,i*.04)
        self.assertGreater(sizer.display_side(.24),270)

    def test_small_measurement_noise_and_width_changes_do_not_resize(self):
        smoother=BoxSmoother()
        sides=[];positions=[]
        for index in range(120):
            measured=face(246+8*np.sin(index),width=100+30*np.cos(index),center=(320+np.sin(index),240+np.cos(index)))
            result=smoother.update([(measured,'Admin',BLUE,.8)],SHAPE,index*.04)
            bounds=square_bounds(result[0][0],SHAPE)
            sides.append(bounds[2]-bounds[0]);positions.append(bounds[:2])
        self.assertEqual(set(sides),{240})
        self.assertLessEqual(max(p[0] for p in positions)-min(p[0] for p in positions),2)

    def test_sustained_distance_changes_in_one_step_each_way(self):
        sizer=DistanceSizer()
        initial=sizer.update(face(246),SHAPE,0)
        # Warm up the filter before testing a real distance change at 25 FPS.
        for index in range(1,8):
            sizer.update(face(246),SHAPE,index*.04)
        far=[sizer.update(face(125),SHAPE,.28+.04*i) for i in range(1,41)]
        self.assertLess(far[-1],initial)
        self.assertEqual(len(set(far)),2)
        self.assertLessEqual(next(i for i,side in enumerate(far,1) if side!=initial)*.04,.24)
        near=[sizer.update(face(246),SHAPE,1.88+.04*i) for i in range(1,41)]
        self.assertEqual(near[-1],initial)
        self.assertEqual(len(set(near)),2)
        self.assertLessEqual(next(i for i,side in enumerate(near,1) if side==initial)*.04,.24)

    def test_brief_bad_measurements_do_not_resize(self):
        sizer=DistanceSizer()
        side=sizer.update(face(246),SHAPE,0)
        for index in range(1,80):
            height=120 if index%9 in (0,1) else 246
            self.assertEqual(sizer.update(face(height),SHAPE,index*.04),side)

    def test_hysteresis_prevents_boundary_bounce(self):
        sizer=DistanceSizer()
        sizer.update(face(246),SHAPE,0)
        for index in range(1,31):sizer.update(face(300),SHAPE,index*.04)
        self.assertEqual(sizer.side,288)
        for index in range(31,160):
            # Midpoint between 240/288, with noise; keep the current larger band.
            self.assertEqual(sizer.update(face((264+5*np.sin(index))/.98),SHAPE,index*.04),288)

    def test_square_fits_edges_and_stabilized_sizes_are_preserved(self):
        for side in square_levels(SHAPE):
            for center in ((0,0),(640,480),(320,240)):
                x1,y1,x2,y2=square_bounds(face(side,width=side,center=center),SHAPE)
                self.assertEqual(x2-x1,y2-y1)
                self.assertEqual(x2-x1,side)
                self.assertTrue(0<=x1<x2<=640 and 0<=y1<y2<=480)

    def test_short_loss_keeps_size_but_never_keeps_old_label(self):
        smoother=BoxSmoother()
        measured=face(246)
        result=smoother.update([(measured,'Admin',BLUE,.8)],SHAPE,0)
        self.assertEqual(smoother.update([],SHAPE,1),[])
        result=smoother.update([(face(270),'Unknown',BLUE,.1)],SHAPE,2)
        self.assertEqual(result[0][1:],('Unknown',BLUE,.1))
        self.assertEqual(result[0][0][2],240)
        self.assertEqual(smoother.update([],SHAPE,7),[])
        result=smoother.update([(face(125),'Admin',BLUE,.8)],SHAPE,7.1)
        self.assertLess(result[0][0][2],240)

    def test_drawn_square_tweens_both_ways_without_requantizing(self):
        smoother=BoxSmoother()
        def rendered(height, now):
            raw=face(height)
            unchanged=raw.copy()
            shown=smoother.update([(raw,'Admin',BLUE,.8)],SHAPE,now)[0][0]
            np.testing.assert_array_equal(raw,unchanged)
            bounds=square_bounds(shown,SHAPE)
            self.assertEqual(bounds[2]-bounds[0],bounds[3]-bounds[1])
            self.assertEqual(bounds[2]-bounds[0],round(float(shown[3])))
            return bounds[2]-bounds[0]
        for index in range(8):rendered(246,index*.04)
        far=[rendered(125,.28+index*.04) for index in range(1,26)]
        self.assertEqual(far[-1],134)
        self.assertTrue(all(a>=b for a,b in zip(far,far[1:])))
        self.assertGreater(len(set(far)),3)
        self.assertEqual(far[11],134)  # Settles within 480 ms including confirmation.
        near=[rendered(246,1.28+index*.04) for index in range(1,26)]
        self.assertEqual(near[-1],240)
        self.assertTrue(all(a<=b for a,b in zip(near,near[1:])))
        self.assertGreater(len(set(near)),3)
        self.assertEqual(near[11],240)

    def test_retarget_during_transition_starts_at_current_display_size(self):
        sizer=DistanceSizer(hold=0)
        sizer.update(face(246),SHAPE,0)
        sizer.update(face(125),SHAPE,.01)
        sizer.update(face(125),SHAPE,.02)
        sizer.update(face(125),SHAPE,.03)
        sizer.update(face(350),SHAPE,.04)
        sizer.update(face(350),SHAPE,.05)
        visible=sizer.display_side(.06)
        sizer.update(face(350),SHAPE,.06)
        self.assertAlmostEqual(sizer.display_side(.06),visible)
        self.assertGreater(sizer.display_side(.15),visible)
        self.assertAlmostEqual(sizer.display_side(.24),sizer.side)


if __name__=='__main__':unittest.main()
