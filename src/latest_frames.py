"""Consume the camera continuously without queuing old frames behind inference.

Only the newest in-memory frame is retained. The reader never runs inference;
its rate drops to four reads/second with the physical shutter's low-power mode.
"""
import threading
import time


class LatestFrames:
    def __init__(self, camera, stop, on_frame=None):
        self.camera,self.stop=camera,stop
        self.on_frame=on_frame
        self.condition=threading.Condition()
        self.closed=threading.Event()
        self.latest=None;self.version=0;self.consumed=0;self.error=False
        self.interval=0.
        self.skipped=0
        self.thread=threading.Thread(target=self.capture,name='vision-camera-latest',daemon=True)
        self.thread.start()

    def set_interval(self, seconds):self.interval=max(0.,seconds)

    def capture(self):
        try:
            while not self.stop.is_set() and not self.closed.is_set():
                started=time.monotonic()
                ok,frame=self.camera.read()
                stamp=time.monotonic()
                if not ok:
                    with self.condition:self.error=True;self.condition.notify_all()
                    return
                with self.condition:
                    self.latest=(frame,stamp);self.version+=1;self.condition.notify_all()
                if self.on_frame is not None:self.on_frame(frame,stamp)
                self.closed.wait(max(.001,self.interval-(time.monotonic()-started)))
        except Exception:
            with self.condition:self.error=True;self.condition.notify_all()

    def read(self):
        with self.condition:
            while not self.stop.is_set() and not self.closed.is_set():
                if self.error:return False,None,time.monotonic()
                if self.version>self.consumed:
                    self.skipped += max(0, self.version-self.consumed-1)
                    self.consumed=self.version
                    frame,stamp=self.latest
                    return True,frame,stamp
                self.condition.wait(.05)
        return False,None,time.monotonic()

    def close(self, timeout=None):
        self.closed.set()
        with self.condition:self.condition.notify_all()
        # Called on the model worker, not the UI thread. Release follows the
        # completed read, so OpenCV is never released concurrently with read().
        self.thread.join(timeout)
        if self.thread.is_alive():
            return False
        self.latest=None
        return True
