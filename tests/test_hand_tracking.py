import _bootstrap  # Resolve application imports from any working directory.
from types import SimpleNamespace as NS
import unittest
import numpy as np
from hand_tracking import Hand, HandPresentation, decode_result, draw_hands
from vision_runtime import VisionDiagnostics
import test_interactions


def points(x=120):
    return np.asarray([(x+i*2,100+i*3) for i in range(21)],np.float32)


class HandsTests(unittest.TestCase):
    def test_decode_mirror_confidence_and_missing_results(self):
        landmarks=[NS(x=.25,y=.5) for _ in range(21)]
        result=NS(hand_landmarks=[landmarks],gestures=[[NS(category_name='Open_Palm',score=.9)]])
        hands=decode_result(result,(480,640,3))
        np.testing.assert_allclose(hands[0].points[0],(479,240))
        self.assertEqual(hands[0].gesture,'Open_Palm')
        for name,score in [('Open_Palm',.4),('None',.99),('unknown',.95),('Victory',float('nan'))]:
            result.gestures=[[NS(category_name=name,score=score)]]
            self.assertEqual(decode_result(result,(480,640,3))[0].gesture,'')
        result.gestures=[]
        self.assertEqual(decode_result(result,(480,640,3))[0].gesture,'')
        result.hand_landmarks=[]
        self.assertEqual(decode_result(result,(480,640,3)),[])

    def test_no_old_gesture_during_change_and_no_hold_after_loss(self):
        smooth=HandPresentation();shape=(480,640,3)
        hand=Hand(0,points(),'Open_Palm',.9)
        self.assertEqual(smooth.update([hand],shape,0)[0].gesture,'')
        self.assertEqual(smooth.update([hand],shape,.13)[0].gesture,'Open_Palm')
        fist=Hand(0,points(),'Closed_Fist',.9)
        self.assertEqual(smooth.update([fist],shape,.15)[0].gesture,'')
        self.assertEqual(smooth.update([fist],shape,.28)[0].gesture,'Closed_Fist')
        self.assertEqual(smooth.update([],shape,.29),[])
        self.assertEqual(smooth.update([fist],shape,.30)[0].gesture,'')
        self.assertEqual(smooth.update([fist],shape,.45)[0].gesture,'Closed_Fist')
        self.assertEqual(smooth.update([fist],shape,1)[0].gesture,'')  # stalled frame breaks continuity

    def test_two_hands_keep_independent_gestures_when_order_changes(self):
        smooth=HandPresentation();shape=(480,640,3)
        palm=Hand(0,points(100),'Open_Palm',.9)
        fist=Hand(0,points(420),'Closed_Fist',.95)
        first=smooth.update([palm,fist],shape,0)
        second=smooth.update([fist,palm],shape,.13)
        self.assertEqual([h.number for h in second],[first[1].number,first[0].number])
        self.assertEqual([h.gesture for h in second],['Closed_Fist','Open_Palm'])
        smooth.reset()
        self.assertEqual(smooth.update([palm],shape,.20)[0].gesture,'')

    def test_overlay_and_smoothing_are_bounded(self):
        smooth=HandPresentation();shape=(480,640,3)
        first=smooth.update([Hand(0,points(),'Victory',.95)],shape,0)[0]
        second=smooth.update([Hand(0,points(140),'Victory',.95)],shape,.04)[0]
        self.assertGreater(second.points[0,0],first.points[0,0])
        self.assertLess(second.points[0,0],140)
        frame=np.zeros(shape,np.uint8)
        self.assertIs(draw_hands(frame,[second]),frame)
        self.assertGreater(frame.sum(),0)
        blank=np.zeros(shape,np.uint8)
        np.testing.assert_array_equal(draw_hands(blank,[]),0)

    def test_diagnostics_clear_disabled_failed_hands_and_copy_metadata(self):
        state=VisionDiagnostics()
        state.set_hands('TRACKED',12,[Hand(1,points(),'Victory',.9)])
        snapshot=state.snapshot()
        self.assertNotIn('points',snapshot['hands'][0])
        snapshot['hands'][0]['gesture']='mutated'
        self.assertEqual(state.snapshot()['hands'][0]['gesture'],'Victory')
        state.set_hands_enabled(False)
        state.set_hands('TRACKED',12,[Hand(1,points(),'Victory',.9)])
        self.assertEqual(state.snapshot()['hands'],[])
        self.assertEqual(state.snapshot()['hands_state'],'OFF')
        state.set_hands_enabled(True)
        state.fail('offline')
        self.assertEqual(state.snapshot()['hands'],[])


class HandCommandTests(unittest.TestCase):
    setUp = test_interactions.InteractionCommandTests.setUp
    def test_hands_help_toggle_invalid_and_fresh_guard(self):
        self.assertIn('vision hands',self.engine.begin('help').text)
        self.assertIn('OFF',self.engine.begin('vision hands off').text)
        self.assertFalse(self.engine.vision.snapshot()['hands_enabled'])
        self.assertIn('ON',self.engine.begin('vision hands on').text)
        self.assertIn('HAND TRACKING',self.engine.begin('vision status').text)
        for command in ('vision hands maybe','vision hands off extra','sudo vision hands on'):
            with self.assertRaises(ValueError):self.engine.begin(command)
        self.allowed=False
        with self.assertRaises(ValueError):self.engine.begin('vision hands off')


if __name__=='__main__':unittest.main()
