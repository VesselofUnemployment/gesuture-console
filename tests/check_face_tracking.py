"""Run actual YuNet + MediaPipe on the user supplied film reference. No camera."""
import _bootstrap  # Resolve application imports from any working directory.
import argparse
import importlib
import time
import cv2
import numpy as np
from face_tracking import FaceTracker, matches_face


def main():
    parser=argparse.ArgumentParser(description='Test actual local face tracker without opening the camera.')
    parser.add_argument('--image', help='Optional reference image with one face.')
    args=parser.parse_args()
    tracker=FaceTracker()
    try:
        blank=np.zeros((480,640,3),np.uint8)
        assert tracker.measure(blank,[100,100,200,250],0.) is None
        if not args.image:
            print('Actual model loaded. Blank frame rejected. Camera was not opened; no photo was saved.')
            return
        image=cv2.imread(args.image)
        if image is None:raise RuntimeError('Reference image is not available.')
        detector,_=importlib.import_module('face_enrollment').load_models()
        detector.setInputSize((image.shape[1],image.shape[0]))
        _,faces=detector.detect(image)
        if faces is None or len(faces)!=1:raise RuntimeError('Reference face could not be detected.')
        samples=[]
        for i in range(6):
            started=time.perf_counter()
            points=tracker.measure(image,faces[0],.1+i*.04)
            samples.append((time.perf_counter()-started)*1000)
            assert points is not None and points.shape==(478,2)
            assert matches_face(points,faces[0])
        print('Real model: 478 measured points on film reference. Mean time %.1fms.' % np.mean(samples[1:]))
        assert tracker.measure(np.zeros_like(image),faces[0],1.) is None
        print('Blank frame: no fabricated face points. Camera was not opened; no photo was saved.')
    finally:tracker.close()


if __name__=='__main__':main()
