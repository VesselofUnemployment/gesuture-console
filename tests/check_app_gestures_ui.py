"""Real worker/Tk/notice with temporary identities and simulated hands; never starts real apps."""
import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
import queue
import time
import tkinter as tk
from unittest.mock import patch
import numpy as np
from app_gestures import ACTION_NAMES
from app_launcher import DEFAULT_BINDINGS
from hand_tracking import Hand
from person_store import save_person
from profile_store import ProfileRepository
from _ui_helpers import close_app
from recognition_ui import RecognitionWindow
from test_registration import vector
from test_vision_features import NewRecognizer
from check_vision_features_ui import Camera
from check_hands_ui import OptionalFace
from check_face_animation_ui import Tracker


class Tray:
    apps_enabled=True
    def __init__(self,callback):self.callback=callback;self.closed=False
    def close(self):self.closed=True
    def set_tip(self,text):pass


class Launcher:
    names = ACTION_NAMES
    bindings = DEFAULT_BINDINGS

    def __init__(self):self.calls=[];self.results=queue.SimpleQueue();self.closed=False
    def request(self,action):
        self.calls.append(action);self.results.put((action,True,''));return True
    def close(self):self.closed=True


class Hands:
    count=None
    gesture='Thumb_Up'
    closed=False
    def measure(self,frame,now):
        return [Hand(1,np.asarray([(380+i*2,160+i*4) for i in range(21)],np.float32),self.gesture,.95,self.count)]
    def reset(self):pass
    def close(self):self.closed=True


def main():
    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'absent.npz'
        for index,(name,role) in enumerate((('Manager','ADMIN'),('Visitor','USER'),('Helper','SUPERVISOR'),('Operator','OPERATOR'))):
            save_person(name,('Software',),role,[vector(index)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy)
        before={p:p.read_bytes() for p in directory.glob('*.npz')}
        camera=Camera();face=Tracker();hands=Hands();detector=OptionalFace();model=NewRecognizer(0);launcher=Launcher()
        root=tk.Tk();root.withdraw()
        with patch('recognition_ui.cv2.VideoCapture',return_value=camera) as opened, \
             patch('recognition_ui.FaceTracker',return_value=face), \
             patch('recognition_ui.HandTracker',return_value=hands), \
             patch('recognition_ui.AppLauncher',return_value=launcher), \
             patch('soft_sounds.winsound.PlaySound'):
            app=RecognitionWindow(root,detector,model,repo=repo,background=True,tray_factory=Tray)
            def pump(seconds):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:root.update();time.sleep(.015)
            def until(condition,seconds=4):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    root.update()
                    if condition():return
                    time.sleep(.015)
                raise AssertionError('App gesture timeout: '+str(app.diagnostics.snapshot()))
            def neutral():hands.count=None;hands.gesture='Thumb_Up';pump(.65)
            try:
                until(lambda:app.gate.candidate is not None)
                for action in ('1','2','3','4','5','fist'):
                    neutral()
                    hands.count=0 if action=='fist' else int(action)
                    hands.gesture='Closed_Fist' if action=='fist' else ''
                    until(lambda:launcher.calls and launcher.calls[-1]==action)
                    count=len(launcher.calls);pump(1.15)
                    assert len(launcher.calls)==count
                    assert root.state()=='withdrawn' and not app.visible.is_set()
                assert launcher.calls==['1','2','3','4','5','fist']
                assert 'Task Manager' in app.diagnostics.snapshot()['app_action']
                # Native menu toggle disables only app actions. I Love You still opens the camera.
                app.actions.put('apps-toggle');pump(.1);neutral()
                assert not app.tray.apps_enabled
                hands.count=1;hands.gesture='';pump(1.3);assert len(launcher.calls)==6
                hands.count=None;hands.gesture='ILoveYou'
                until(lambda:app.visible.is_set())
                app.hide_window();neutral()
                app.actions.put('apps-toggle');pump(.1)
                assert app.tray.apps_enabled
                for index in (1,2,3):
                    model.index=index;neutral();hands.count=3;hands.gesture='';pump(1.3)
                    assert len(launcher.calls)==6 and app.terminal is None
                model.index=1;neutral();hands.gesture='ILoveYou';pump(1.)
                assert not app.visible.is_set(),'USER can still wake via an unauthorized hand'
                model.index=3;neutral();hands.gesture='ILoveYou'
                until(lambda:app.visible.is_set())
                app.hide_window();neutral()
                model.index=0;neutral();app.show_window();app.open_panel();root.update()
                assert app.terminal is not None
                app.terminal.entry.insert(0,'vision ap');app.terminal.complete()
                assert app.terminal.entry.get()=='vision apps'
                hands.count=4;hands.gesture='';pump(1.3);assert len(launcher.calls)==6
                app.terminal.close();neutral()
                detector.visible=False;pump(3.15);hands.count=5;hands.gesture='';pump(1.3)
                assert len(launcher.calls)==6
                assert opened.call_count==1
                assert all(p.read_bytes()==value for p,value in before.items())
            finally:close_app(app)
        assert camera.released and hands.closed and face.closed and launcher.closed
        assert not app.worker.is_alive()
    print('App UI passed: six bindings exactly once for ADMIN, hidden inference, tray toggle, OPERATOR+ I Love You and USER denial, supervisor/operator app shortcut denial, bounded grace, panel exclusion, Tab, one camera, unchanged templates and cleanup.')


if __name__=='__main__':main()
