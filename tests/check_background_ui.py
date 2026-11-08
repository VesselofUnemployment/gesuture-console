"""Real Tk, worker and Windows tray; temporary profiles and a simulated camera/pose."""
import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import tkinter as tk
from unittest.mock import patch
import numpy as np
from hand_tracking import Hand
from person_store import save_person
from profile_store import ProfileRepository
from _ui_helpers import close_app
from recognition_ui import RecognitionWindow
from windows_tray import NativeTray, CALLBACK, HIDE, QUIT
from background_runtime import SingleInstance
from test_registration import vector
from test_vision_features import NewRecognizer
from check_vision_features_ui import Camera
from check_hands_ui import OptionalFace
from check_face_animation_ui import Tracker


class Hands:
    gesture='Victory'
    calls=0
    closed=False
    def measure(self,frame,now):
        self.calls+=1
        return [Hand(1,np.asarray([(380+i*2,160+i*4) for i in range(21)],np.float32),self.gesture,.9)]
    def reset(self):pass
    def close(self):self.closed=True


def main():
    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'absent.npz'
        save_person('Manager',('Software',),'ADMIN',[vector(0)]*3,directory,legacy)
        save_person('Visitor',('Design',),'USER',[vector(1)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy)
        camera=Camera();face=Tracker();hands=Hands();detector=OptionalFace();model=NewRecognizer(0)
        root=tk.Tk();root.withdraw()
        with SingleInstance(temporary) as instance, \
             patch('recognition_ui.cv2.VideoCapture',return_value=camera) as opened, \
             patch('recognition_ui.FaceTracker',return_value=face), \
             patch('recognition_ui.HandTracker',return_value=hands), \
             patch('soft_sounds.winsound.PlaySound') as sound:
            app=RecognitionWindow(root,detector,model,repo=repo,background=True,instance=instance)
            def pump(seconds):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    root.update();time.sleep(.01)
            def until(condition,seconds=5):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    root.update()
                    if condition():return
                    time.sleep(.01)
                raise AssertionError('Background UI timeout: '+str(app.diagnostics.snapshot()))
            try:
                assert isinstance(app.tray,NativeTray), 'Native tray must work; fallback is not a pass'
                until(lambda:camera.reads>12 and app.gate.candidate is not None)
                assert root.state()=='withdrawn' and not app.visible.is_set()
                assert app.photo is None and face.calls==0 and hands.calls>10
                assert not sound.called
                detector.visible=False;pump(3.15);hands.gesture='ILoveYou'
                pump(.95)
                assert root.state()=='withdrawn', 'Unknown face must not wake window'
                detector.visible=True
                until(lambda:app.visible.is_set())
                until(lambda:app.photo is not None)
                assert root.state()=='normal'
                app.open_panel();root.update();assert app.terminal is not None
                app.request_close();root.update()
                assert root.state()=='withdrawn' and app.terminal is None and app.gate.owner is None
                reads=camera.reads;pump(.9)
                assert camera.reads>reads+5 and root.state()=='withdrawn', 'Held pose cannot undo hide'
                hands.gesture='Victory';pump(.65);hands.gesture='ILoveYou'
                until(lambda:app.visible.is_set())
                app.hide_window();hands.gesture='Victory'
                with SingleInstance(temporary) as second:
                    assert not second.primary
                    second.activate()
                    until(lambda:app.visible.is_set())
                # Real native message callbacks are dispatched by Tk, with no extra window.
                tray=app.tray
                tray.user.PostMessageW(tray.hwnd,0x111,HIDE,0)
                until(lambda:not app.visible.is_set())
                tray.user.PostMessageW(tray.hwnd,CALLBACK,0,0x400 | (1<<16))
                until(lambda:app.visible.is_set())
                assert opened.call_count==1
                assert all(child is app.notice.window for child in root.winfo_children()
                           if isinstance(child,tk.Toplevel))
                model.index=1
                until(lambda:app.gate.candidate is None)
                assert app.canvas.itemcget(app.button_item,'state')=='hidden'
                tray.user.PostMessageW(tray.hwnd,0x111,QUIT,0)
                until(lambda:app.closed)
            finally:
                if not app.closed:close_app(app)
        assert camera.released and face.closed and hands.closed and not app.worker.is_alive()
        assert tray.hwnd is None and not tray.added
    print('Background UI passed: hidden inference/no rendering or sound, deliberate pose, identity gate, hide/rearm, single instance/camera, native tray callbacks, permissions, full cleanup.')


if __name__=='__main__':main()
