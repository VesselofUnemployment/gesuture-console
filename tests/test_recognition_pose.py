"""Narrow detections must reach matching, without weakening enrollment/identity."""
import _bootstrap
import importlib
import unittest
import numpy as np
from person_store import Person
from test_registration import vector

recognition=importlib.import_module('recognize')
enrollment=importlib.import_module('face_enrollment')


class Recognizer:
    def __init__(self):self.calls=0
    def alignCrop(self,*args):
        return np.random.default_rng(0).integers(70,180,(112,112,3),dtype=np.uint8)
    def feature(self,*args):self.calls+=1;return vector(0)


class RecognitionPoseTests(unittest.TestCase):
    def setUp(self):
        self.frame=np.zeros((480,640,3),np.uint8)
        self.face=np.array([150,100,70,180,170,140,200,140,185,170,170,200,200,200,.99],np.float32)
        self.person=Person('synthetic','Synthetic',('Software',),'ADMIN',np.stack([vector(0)]*3))

    def test_narrow_detection_reaches_real_matching_policy(self):
        model=Recognizer();metrics=[]
        label,_,score=recognition.analyze_face(self.frame,self.face,model,[self.person],.65,metrics)
        self.assertEqual(model.calls,1)
        self.assertEqual(label,'synthetic')
        self.assertEqual(metrics[0]['reason'],'MATCHED')
        self.assertAlmostEqual(score,1)

    def test_enrollment_remains_strict(self):
        self.assertIsNone(enrollment.select_face(np.array([self.face]),self.frame.shape)[0])

    def test_small_invalid_or_cropped_detection_cannot_reach_matching(self):
        candidates=[]
        for index,value in ((2,59),(3,99),(0,-1),(0,620),(4,float('nan'))):
            face=self.face.copy();face[index]=value;candidates.append(face)
        candidates.append(self.face[:4])
        for face in candidates:
            with self.subTest(face=face):
                model=Recognizer();metrics=[]
                label,_,score=recognition.analyze_face(self.frame,face,model,[self.person],.65,metrics)
                self.assertEqual(model.calls,0)
                self.assertIsNone(score)
                self.assertEqual(metrics[0]['reason'],'SIZE_OR_CROP')

    def test_quality_and_match_threshold_still_apply(self):
        class Blurred(Recognizer):
            def alignCrop(self,*args):return np.full((112,112,3),120,np.uint8)
        model=Blurred();metrics=[]
        recognition.analyze_face(self.frame,self.face,model,[self.person],.65,metrics)
        self.assertEqual(model.calls,0)
        self.assertEqual(metrics[0]['reason'],'BLUR')
        unknown=Person('other','Other',('Software',),'USER',np.stack([vector(1)]*3))
        metrics=[]
        label,_,score=recognition.analyze_face(self.frame,self.face,Recognizer(),[unknown],.65,metrics)
        self.assertEqual(label,'Unknown')
        self.assertEqual(metrics[0]['reason'],'BELOW_THRESHOLD')


if __name__=='__main__':unittest.main()
