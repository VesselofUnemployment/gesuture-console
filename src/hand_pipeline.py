"""One hand worker with a single latest-frame mailbox, independent of face work.

The model, resets and close all run on this one worker. Invalidating a generation
discards in-flight results and pending images; nothing is written to disk.
"""
import threading
import time


class HandPipeline:
    def __init__(self, tracker, publish, diagnostics):
        self.tracker=tracker;self.publish=publish;self.diagnostics=diagnostics
        self.condition=threading.Condition();self.pending=None;self.latest=[]
        self.epoch=0;self.key=None;self.enabled=False;self.closed=False;self.reset_needed=False
        self.last_offered=float('-inf')
        self.skipped=0
        self.thread=threading.Thread(target=self.run,name='vision-hands-latest',daemon=True)
        self.thread.start()

    def configure(self, enabled, generation):
        with self.condition:
            key=(bool(enabled),generation)
            if key!=self.key:
                self.key=key;self.enabled=bool(enabled);self.epoch+=1
                self.pending=None;self.latest=[];self.reset_needed=True
                self.last_offered=float('-inf')
                self.condition.notify_all()

    def offer(self, frame, captured, generation):
        with self.condition:
            if self.enabled and not self.closed and generation==self.key[1] and captured>self.last_offered:
                self.last_offered=captured
                if self.pending is not None:
                    self.skipped += 1
                self.pending=(frame,captured,generation,self.epoch)
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:return list(self.latest)

    def run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda:self.closed or self.reset_needed or self.pending is not None)
                    if self.closed:return
                    if self.reset_needed:
                        self.tracker.reset();self.reset_needed=False
                    job=self.pending;self.pending=None
                if job is None:continue
                frame,captured,generation,epoch=job
                started=time.monotonic();error=''
                try:
                    hands=self.tracker.measure(frame,captured)
                    state='TRACKED' if hands else 'SEARCHING'
                except Exception as failure:
                    hands=[];state='ERROR';error=str(failure);self.tracker.reset()
                finished=time.monotonic()
                with self.condition:
                    if self.closed or not self.enabled or epoch!=self.epoch:continue
                    self.latest=list(hands)
                    self.diagnostics.set_hands(state,(finished-started)*1000,hands,error)
                    self.diagnostics.set_latency(hand_wait_ms=(started-captured)*1000,
                        hand_result_ms=(finished-captured)*1000)
                    # This callback only puts into a mailbox; it never calls Tk/Win32.
                    self.publish(hands,frame.shape,captured,generation,finished)
        finally:
            self.tracker.close()

    def close(self):
        with self.condition:
            self.closed=True;self.pending=None;self.latest=[];self.epoch+=1
            self.condition.notify_all()
        self.thread.join()
