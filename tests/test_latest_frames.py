import _bootstrap
import threading
import time
import unittest
from latest_frames import LatestFrames


class Camera:
    def __init__(self):self.reads=0;self.value=0;self.failed=False
    def read(self):
        time.sleep(.004)
        self.reads+=1;self.value+=1
        return not self.failed,self.value


class LatestTests(unittest.TestCase):
    def test_slow_model_gets_latest_frame_not_the_next_buffered_frame(self):
        camera=Camera();stop=threading.Event();frames=LatestFrames(camera,stop)
        try:
            ok,first,stamp=frames.read();self.assertTrue(ok)
            # Let several frames arrive during a simulated expensive inference;
            # Windows timer granularity must not determine this test's outcome.
            deadline=time.monotonic()+1.
            while camera.value<first+7 and time.monotonic()<deadline:time.sleep(.01)
            ok,second,stamp=frames.read()
            self.assertTrue(ok);self.assertGreater(second,first+5)
            self.assertLess(time.monotonic()-stamp,.1)
            self.assertLessEqual(camera.value-second,2)
        finally:stop.set();frames.close()
        self.assertFalse(frames.thread.is_alive())

    def test_low_power_throttles_camera_and_stop_unblocks_waiter(self):
        camera=Camera();stop=threading.Event();frames=LatestFrames(camera,stop)
        try:
            frames.set_interval(.25);frames.read();time.sleep(.05)
            before=camera.reads;time.sleep(.54)
            self.assertLessEqual(camera.reads-before,3)
            frames.read();result=[]
            waiter=threading.Thread(target=lambda:result.append(frames.read()))
            waiter.start();stop.set();waiter.join(.2)
            self.assertFalse(waiter.is_alive());self.assertFalse(result[0][0])
        finally:stop.set();frames.close()

    def test_camera_error_does_not_reuse_old_frame(self):
        camera=Camera();stop=threading.Event();frames=LatestFrames(camera,stop)
        try:
            frames.read();camera.failed=True;time.sleep(.03)
            ok,frame,stamp=frames.read();self.assertFalse(ok);self.assertIsNone(frame)
        finally:stop.set();frames.close()


if __name__=='__main__':unittest.main()
