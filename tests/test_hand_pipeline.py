import _bootstrap
import threading
import time
import unittest
from hand_pipeline import HandPipeline
from vision_runtime import VisionDiagnostics


class Frame:
    shape=(480,640,3)
    def __init__(self,number):self.number=number


class Tracker:
    def __init__(self):
        self.started=threading.Event();self.release=threading.Event()
        self.calls=[];self.closed=False;self.resets=0;self.threads=set()
    def measure(self,frame,now):
        self.threads.add(threading.get_ident());self.calls.append(frame.number)
        if len(self.calls)==1:
            self.started.set();self.release.wait(2)
        return []
    def reset(self):self.threads.add(threading.get_ident());self.resets+=1
    def close(self):self.threads.add(threading.get_ident());self.closed=True


class PipelineTests(unittest.TestCase):
    def wait(self,condition):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if condition():return
            threading.Event().wait(.002)
        self.fail('Hand worker did not complete')

    def test_latest_only_mailbox_does_not_wait_for_face_thread(self):
        tracker=Tracker();results=[];diagnostics=VisionDiagnostics()
        pipeline=HandPipeline(tracker,lambda *args:results.append(args),diagnostics)
        try:
            pipeline.configure(True,4);stamp=time.monotonic();pipeline.offer(Frame(1),stamp,4)
            self.assertTrue(tracker.started.wait(1))
            for number in (2,3,4):pipeline.offer(Frame(number),stamp+number/30,4)
            tracker.release.set();self.wait(lambda:len(results)==2)
            self.assertEqual(tracker.calls,[1,4])
            self.assertTrue(all(r[3]==4 for r in results))
            self.assertIn('hand_result_ms',diagnostics.snapshot()['latency'])
        finally:tracker.release.set();pipeline.close()
        self.assertTrue(tracker.closed);self.assertFalse(pipeline.thread.is_alive())
        self.assertEqual(len(tracker.threads),1)

    def test_pause_and_revision_discard_inflight_result(self):
        for enabled in (False,True):
            with self.subTest(enabled=enabled):
                tracker=Tracker();results=[]
                pipeline=HandPipeline(tracker,lambda *args:results.append(args),VisionDiagnostics())
                try:
                    pipeline.configure(True,1);pipeline.offer(Frame(1),time.monotonic(),1)
                    self.assertTrue(tracker.started.wait(1))
                    pipeline.configure(enabled,2);tracker.release.set()
                    self.wait(lambda:tracker.resets>=2)
                    self.assertEqual(results,[]);self.assertEqual(pipeline.snapshot(),[])
                    if enabled:
                        pipeline.offer(Frame(2),time.monotonic(),2)
                        self.wait(lambda:len(results)==1)
                        self.assertEqual(results[0][3],2)
                finally:tracker.release.set();pipeline.close()


if __name__=='__main__':unittest.main()
