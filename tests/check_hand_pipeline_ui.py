"""Actual Tk/worker scheduling; counted fake camera/models and no app/window actions."""
import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import tkinter as tk
import time
from unittest.mock import patch
from _ui_helpers import close_app
from recognition_ui import RecognitionWindow
from person_store import save_person
from profile_store import ProfileRepository
from test_registration import vector
from test_vision_features import NewRecognizer
from check_vision_features_ui import Camera
from check_hands_ui import OptionalFace
from check_face_animation_ui import Tracker
from check_app_gestures_ui import Tray, Launcher, Hands


class SlowFace(OptionalFace):
    slow=False
    started=threading.Event()
    def detect(self,frame):
        if self.slow:
            self.started.set();time.sleep(.4)
        return super().detect(frame)


class CountedHands(Hands):
    calls=0
    def measure(self,frame,now):
        self.calls+=1
        return super().measure(frame,now)


def main():
    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'absent.npz'
        save_person('Manager',('Software',),'ADMIN',[vector(0)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy)
        camera=Camera();detector=SlowFace();hands=CountedHands();launcher=Launcher()
        root=tk.Tk();root.withdraw()
        with patch('recognition_ui.cv2.VideoCapture',return_value=camera), \
             patch('recognition_ui.FaceTracker',return_value=Tracker()), \
             patch('recognition_ui.HandTracker',return_value=hands), \
             patch('recognition_ui.AppLauncher',return_value=launcher):
            app=RecognitionWindow(root,detector,NewRecognizer(0),repo=repo,background=True,tray_factory=Tray)
            def pump(seconds):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:root.update();time.sleep(.003)
            def until(condition):
                deadline=time.monotonic()+4
                while time.monotonic()<deadline:
                    root.update()
                    if condition():return
                    time.sleep(.003)
                raise AssertionError(app.diagnostics.snapshot())
            try:
                until(lambda:app.authority.current() is not None and hands.calls>=4)
                detector.slow=True
                until(lambda:detector.started.is_set())
                before=hands.calls;pump(.2)
                assert hands.calls>=before+4,(before,hands.calls)
                latency=app.diagnostics.snapshot()['latency']
                assert latency['read_to_control_ms']<150,latency
                assert latency['hand_ui_ms']<100,latency
                print('Hands remain live during a 400ms face stage; measured read-to-UI/apply:',
                      round(latency['read_to_control_ms'],1),'ms',flush=True)
                app.diagnostics.set_hands_enabled(False);before=hands.calls;pump(.55)
                stopped=hands.calls;pump(.2);assert hands.calls==stopped
                assert launcher.calls==[]
            finally:close_app(app)
        assert camera.released and hands.closed and launcher.closed
    print('Independent hand scheduling, toggle invalidation and joined shutdown passed.')


if __name__=='__main__':main()
