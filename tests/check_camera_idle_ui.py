"""Real worker/UI with counted fake models; no photos or real app launches."""
import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import tkinter as tk
from unittest.mock import patch
import numpy as np
from person_store import save_person
from profile_store import ProfileRepository
from _ui_helpers import close_app
from recognition_ui import RecognitionWindow
from test_registration import vector
from test_vision_features import NewRecognizer
from check_vision_features_ui import Camera
from check_face_animation_ui import Tracker
from check_hands_ui import OptionalFace
from check_app_gestures_ui import Tray, Launcher, Hands


class CountedDetector(OptionalFace):
    calls=0
    def detect(self,frame):
        self.calls+=1
        return super().detect(frame)


class CountedHands(Hands):
    calls=0
    def measure(self,frame,now):
        self.calls+=1
        return super().measure(frame,now)


def main():
    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'absent.npz'
        for index,(name,role) in enumerate((('Manager','ADMIN'),('Visitor','USER'))):
            save_person(name,('Software',),role,[vector(index)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy)
        before={p:p.read_bytes() for p in directory.glob('*.npz')}
        camera=Camera();bright=camera.frame.copy();face=Tracker();hands=CountedHands()
        detector=CountedDetector();model=NewRecognizer(0);launcher=Launcher()
        root=tk.Tk();root.withdraw()
        with patch('recognition_ui.cv2.VideoCapture',return_value=camera) as opened, \
             patch('recognition_ui.FaceTracker',return_value=face), \
             patch('recognition_ui.HandTracker',return_value=hands), \
             patch('recognition_ui.AppLauncher',return_value=launcher), \
             patch('soft_sounds.winsound.PlaySound'):
            app=RecognitionWindow(root,detector,model,repo=repo,background=True,tray_factory=Tray)
            def pump(seconds):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:root.update();time.sleep(.01)
            def until(condition,seconds=5):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    root.update()
                    if condition():return
                    time.sleep(.01)
                raise AssertionError('Idle UI timeout: '+str(app.diagnostics.snapshot()))
            def neutral():hands.count=None;hands.gesture='Thumb_Up';pump(.65)
            try:
                until(lambda:app.gate.candidate is not None and hands.calls>4)
                neutral();detector.visible=False
                # Hand inference stays live while the brief missing face uses the lease.
                hands.count=1;hands.gesture=''
                until(lambda:launcher.calls==['1'],seconds=2.6)
                assert app.gate.current_candidate() is not None
                assert app.canvas.itemcget(app.button_item,'state')=='normal'
                until(lambda:app.gate.current_candidate() is None)
                neutral();hands.count=2;hands.gesture='';pump(1.2)
                assert launcher.calls==['1']
                detector.visible=True;neutral()
                until(lambda:app.gate.candidate is not None)
                model.index=1;hands.count=3;hands.gesture='';pump(1.3)
                assert launcher.calls==['1'] and app.gate.candidate is None
                model.index=0;neutral();app.show_window()
                until(lambda:face.calls>0 and app.photo is not None)
                app.open_panel();root.update();assert app.terminal is not None
                # No model may be called on dark frames, even with the panel visible.
                camera.frame=np.full_like(bright,16)
                until(lambda:app.diagnostics.snapshot()['camera_paused'])
                pump(.35)
                reads=camera.reads;calls=(detector.calls,hands.calls,face.calls)
                pump(1.1)
                assert (detector.calls,hands.calls,face.calls)==calls
                assert 3<=camera.reads-reads<=6,(camera.reads,reads)
                state=app.diagnostics.snapshot()
                assert state['hands']==[] and state['faces']==[] and state['landmarks']==0
                assert 3<=state['fps']<=5
                assert np.max(app.display)==16  # Never leaves the old face/photo on screen.
                assert app.canvas.itemcget(app.message_item,'text')=='CAMERA COVERED / LOW POWER'
                app.terminal.execute('vision status')
                assert 'LOW / dark image' in app.terminal.output.get('1.0','end')
                until(lambda:app.gate.candidate is None)
                until(lambda:app.terminal.locked,seconds=3)
                camera.frame=bright;started=time.monotonic()
                until(lambda:not app.diagnostics.snapshot()['camera_paused'])
                detected=time.monotonic()-started
                until(lambda:not app.diagnostics.snapshot()['camera_paused'] and app.gate.candidate is not None)
                print(f'Uncover detection {detected:.3f}s; UI recognition {time.monotonic()-started:.3f}s',flush=True)
                assert detected<.8
                until(lambda:hands.calls>calls[1])
                assert opened.call_count==1
                assert all(p.read_bytes()==value for p,value in before.items())
                # Stop.wait is interruptible while sleeping in dark mode.
                camera.frame=np.full_like(bright,16)
                until(lambda:app.diagnostics.snapshot()['camera_paused'])
                started=time.monotonic();close_app(app)
                assert time.monotonic()-started<1.2
            finally:
                if not app.closed:close_app(app)
        assert camera.released and hands.closed and face.closed and launcher.closed
        assert not app.worker.is_alive()
    print('Idle UI passed: 3-second authority lease with live hand actions, expiry, other-user rejection, zero model calls in dark mode, 4Hz checks, visible black preview/status, panel timeout, automatic fast resume, one camera, unchanged templates and interruptible shutdown.')


if __name__=='__main__':main()
