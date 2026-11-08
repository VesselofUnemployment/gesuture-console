"""Only a bounded, fresh, same-person continuation can use a lower score."""
import _bootstrap
from dataclasses import replace
import importlib
import unittest
import numpy as np
from person_store import Person
from face_continuation import FaceContinuation
from vision_runtime import VisionDiagnostics


def sample(score):
    vector=np.zeros(128,np.float32);vector[0]=score;vector[2]=(1-score*score)**.5
    return vector


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.now=[0.]
        self.policy=FaceContinuation(clock=lambda:self.now[0])
        a=np.zeros(128,np.float32);a[0]=1
        b=np.zeros(128,np.float32);b[1]=1
        self.a=Person('a','Alpha',('Software',),'ADMIN',np.stack([a]*3))
        self.b=Person('b','Beta',('Software',),'USER',np.stack([b]*3))
        self.people=[self.a,self.b]
        self.face=np.array([120,100,120,180,150,145,200,145,175,175,150,210,200,210,.99],np.float32)

    def match(self,score,at,count=1,generation=0,threshold=.65,face=None,people=None):
        self.now[0]=at
        self.policy.prepare(count,at,threshold,generation)
        return self.policy.match(sample(score),self.face if face is None else face,
                                 self.people if people is None else people,threshold)

    def seed(self):self.assertEqual(self.match(.8,0)[0].identity,'a')

    def test_initial_match_needs_high_threshold(self):
        self.assertIsNone(self.match(.64,0)[0])
        self.assertEqual(self.policy.decision['effective_threshold'],.65)

    def test_continuous_face_accepts_real_lower_match(self):
        self.seed()
        self.assertEqual(self.match(.62,.1)[0].identity,'a')
        self.assertEqual(self.policy.decision['reason'],'MATCHED_CONTINUATION')
        self.assertAlmostEqual(self.policy.decision['effective_threshold'],.60)

    def test_low_matches_never_renew_the_strong_anchor(self):
        self.seed()
        for at in (.2,.4,.6,.8,1.):self.assertIsNotNone(self.match(.62,at)[0])
        self.assertEqual(self.policy.anchor,0)
        self.assertIsNone(self.match(.62,1.01)[0])

    def test_strong_match_renews_anchor(self):
        self.seed();self.match(.8,.2)
        for at in (.4,.6,.8,1.,1.15):self.assertIsNotNone(self.match(.62,at)[0])
        self.assertEqual(self.policy.anchor,.2)

    def test_no_face_or_multiple_faces_require_reacquisition(self):
        for count in (0,2):
            with self.subTest(count=count):
                self.setUp();self.seed()
                self.policy.prepare(count,.05,.65,0,now=.05)
                self.assertIsNone(self.match(.62,.1)[0])

    def test_missing_updates_cannot_continue(self):
        self.seed();self.assertIsNone(self.match(.62,.3)[0])

    def test_position_or_scale_jump_requires_reacquisition(self):
        for index,value in ((0,420),(2,50),(3,350)):
            with self.subTest(index=index):
                self.setUp();self.seed();face=self.face.copy();face[index]=value
                self.assertIsNone(self.match(.62,.1,face=face)[0])

    def test_metadata_generation_and_threshold_changes_reset_context(self):
        for changes in (dict(people=[replace(self.a,role='USER'),self.b]),
                        dict(people=[replace(self.a,revision=1),self.b]),
                        dict(people=[replace(self.a,active=False),self.b]),
                        dict(generation=1),dict(threshold=.66)):
            with self.subTest(changes=changes):
                self.setUp();self.seed()
                self.assertIsNone(self.match(.62,.1,**changes)[0])

    def test_weak_other_identity_cannot_inherit_context(self):
        self.seed();self.now[0]=.1;self.policy.prepare(1,.1,.65,0)
        vector=np.zeros(128,np.float32);vector[1]=.62;vector[2]=(1-.62**2)**.5
        self.assertIsNone(self.policy.match(vector,self.face,self.people,.65)[0])
        self.assertIsNone(self.policy.token)

    def test_ambiguity_margin_is_still_required(self):
        self.seed()
        vector=np.zeros(128,np.float32);vector[0]=.62;vector[1]=.60;vector[2]=(1-.62**2-.60**2)**.5
        self.now[0]=.1;self.policy.prepare(1,.1,.65,0)
        self.assertIsNone(self.policy.match(vector,self.face,self.people,.65)[0])
        self.assertEqual(self.policy.decision['reason'],'AMBIGUOUS')

    def test_below_floor_rejects_and_resets(self):
        self.seed();self.assertIsNone(self.match(.59,.1)[0])
        self.assertIsNone(self.match(.62,.2)[0])

    def test_old_processing_result_cannot_seed_continuation(self):
        self.policy.prepare(1,0,.65,0,now=0)
        self.now[0]=.7
        self.policy.match(sample(.8),self.face,self.people,.65)
        self.assertIsNone(self.policy.token)
        self.assertIsNone(self.match(.62,.71)[0])

    def test_strong_result_in_multi_face_frame_cannot_seed_context(self):
        self.match(.8,0,count=2)
        self.assertIsNone(self.match(.62,.1)[0])

    def test_blurry_frame_resets_context_without_extra_inference(self):
        self.seed();self.now[0]=.1;self.policy.prepare(1,.1,.65,0)
        class Blurry:
            def alignCrop(self,*args):return np.full((112,112,3),120,np.uint8)
            def feature(self,*args):raise AssertionError('Blur must be rejected before inference')
        module=importlib.import_module('recognize');details=[]
        module.analyze_face(np.zeros((480,640,3),np.uint8),self.face,Blurry(),self.people,.65,details,self.policy)
        self.assertEqual(details[0]['reason'],'BLUR')
        self.assertIsNone(self.match(.62,.2)[0])

    def test_summary_is_bounded_and_contains_no_identity(self):
        diagnostics=VisionDiagnostics()
        for _ in range(600):
            diagnostics.record((480,640,3),[dict(identity='PRIVATE_NAME',reason='BELOW_THRESHOLD',score=.62)],1)
        summary=diagnostics.face_summary()
        self.assertEqual(summary['window_frames'],512)
        self.assertEqual(summary['reasons'],{'BELOW_THRESHOLD':512})
        self.assertNotIn('PRIVATE_NAME',str(summary))
        diagnostics.record((480,640,3),[],1)
        self.assertEqual(diagnostics.face_summary()['reasons']['NO_FACE'],1)


if __name__=='__main__':unittest.main()
