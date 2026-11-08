"""Real local model check. Does not open a camera or save camera images."""
import _bootstrap  # Resolve application imports from any working directory.
import argparse
from pathlib import Path
import statistics
import time
import cv2
import numpy as np
from hand_tracking import HandTracker


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--samples',type=Path,help='Optional folder containing the official sample JPEGs')
    args=parser.parse_args()
    tracker=HandTracker()
    stamp=time.monotonic()
    try:
        assert tracker.measure(np.zeros((480,640,3),np.uint8),stamp)==[]
        print('Real model: blank frame rejected; no fabricated hand points.')
        if args.samples:
            checked=0
            for filename,expected in (('thumbs_up.jpg','Thumb_Up'),('thumbs_down.jpg','Thumb_Down'),
                                      ('victory.jpg','Victory'),('pointing_up.jpg','Pointing_Up')):
                if not (args.samples/filename).is_file():continue
                frame=cv2.imread(str(args.samples/filename))
                if frame is None:raise ValueError('Cannot read sample: '+filename)
                h,w=frame.shape[:2]
                frame=cv2.resize(frame,(round(w*min(640/w,480/h)),round(h*min(640/w,480/h))))
                tracker.reset();times=[]
                for index in range(10):
                    stamp+=.04
                    started=time.perf_counter();hands=tracker.measure(frame,stamp)
                    times.append((time.perf_counter()-started)*1000)
                assert any(h.gesture==expected and h.points.shape==(21,2) for h in hands),(filename,hands)
                checked+=1
                print(f'{filename}: {expected}; {len(hands)} hand(s), 21 measured points/hand; median {statistics.median(times):.1f}ms')
                stamp+=.04
                assert tracker.measure(np.zeros_like(frame),stamp)==[]
            assert checked,'No official sample files found.'
            print(f'{checked} available official sample poses checked.')
            pointing=args.samples/'pointing_up.jpg'
            if pointing.is_file():
                sample=cv2.resize(cv2.imread(str(pointing)),(320,320))
                pair=np.hstack([sample,sample])
                tracker.reset()
                for index in range(8):
                    stamp+=.04;hands=tracker.measure(pair,stamp)
                assert len(hands)==2 and all(h.gesture=='Pointing_Up' for h in hands)
                print('Two-hand composite: two independent sets of 21 points and Pointing_Up poses.')
        print('Hand tracking checks passed. Live user camera accuracy has not been measured.')
    finally:tracker.close()


if __name__=='__main__':main()
