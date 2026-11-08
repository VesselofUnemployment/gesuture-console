"""Shared camera, gesture toggles, permissions and cleanup with temporary profiles."""
import _bootstrap  # Resolve application imports from any working directory.
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
from test_registration import vector
from test_vision_features import NewRecognizer
from check_vision_features_ui import Camera, Detector
from check_face_animation_ui import Tracker
from desktop_control import DesktopControl
from test_desktop_control import Backend, Overlay


class Hands:
    def __init__(self):
        self.calls=0;self.closed=False;self.visible=True;self.broken=False;self.resets=0
    def measure(self,frame,now):
        self.calls+=1
        if self.broken:raise RuntimeError('Test hand failure')
        if not self.visible:return []
        return [Hand(1,np.asarray([(380+i*2,160+i*4) for i in range(21)],np.float32),'Victory',.9)]
    def reset(self):self.resets+=1
    def close(self):self.closed=True


class OptionalFace(Detector):
    visible=True
    def detect(self,frame):
        return super().detect(frame) if self.visible else (None,None)


def main():
    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'absent.npz'
        save_person('Manager',('Software',),'ADMIN',[vector(0)]*3,directory,legacy)
        save_person('Visitor',('Design',),'USER',[vector(1)]*3,directory,legacy)
        save_person('Operator',('Software',),'OPERATOR',[vector(2)]*3,directory,legacy)
        save_person('Supervisor',('Software',),'SUPERVISOR',[vector(3)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy)
        before={path:path.read_bytes() for path in directory.glob('*.npz')}
        camera=Camera();face=Tracker();hands=Hands();model=NewRecognizer(0);detector=OptionalFace();root=tk.Tk()
        with patch('recognition_ui.cv2.VideoCapture',return_value=camera) as opened, \
             patch('recognition_ui.FaceTracker',return_value=face), \
             patch('recognition_ui.HandTracker',return_value=hands), \
             patch('soft_sounds.winsound.PlaySound'):
            app=RecognitionWindow(root,detector,model,repo=repo)
            def until(condition,seconds=6):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    root.update()
                    if condition():return
                    time.sleep(.015)
                raise AssertionError('Hands UI timeout: '+str(app.diagnostics.snapshot()))
            try:
                until(lambda:app.gate.candidate is not None and app.diagnostics.snapshot()['hands'])
                assert app.diagnostics.snapshot()['hands'][0]['gesture']=='Victory'
                app.open_panel();root.update()
                terminal=app.terminal
                resets=hands.resets
                terminal.execute('vision hands off')
                until(lambda:app.diagnostics.snapshot()['hands_state']=='OFF')
                until(lambda:hands.resets>resets)
                reads,calls=camera.reads,hands.calls
                until(lambda:camera.reads>=reads+4)
                assert hands.calls==calls
                terminal.entry.insert(0,'vision h');terminal.complete()
                assert terminal.entry.get()=='vision hands'
                terminal.entry.delete(0,'end');terminal.entry.insert(0,'vision hands o');terminal.complete()
                assert terminal.entry.get() in ('vision hands on','vision hands off')
                terminal.entry.delete(0,'end')
                terminal.execute('vision hands on')
                until(lambda:app.diagnostics.snapshot()['hands_state']=='TRACKED')
                terminal.execute('vision status')
                assert 'HAND GESTURES' in terminal.output.get('1.0','end')
                hands.visible=False
                until(lambda:app.diagnostics.snapshot()['hands_state']=='SEARCHING')
                assert app.diagnostics.snapshot()['hands']==[]
                hands.broken=True
                until(lambda:app.diagnostics.snapshot()['hands_state']=='ERROR')
                assert app.diagnostics.snapshot()['state']=='ONLINE'
                assert app.gate.candidate is not None  # Hand errors do not break face authentication.
                hands.broken=False;hands.visible=True
                until(lambda:app.diagnostics.snapshot()['hands_state']=='TRACKED')
                terminal.close();root.update()
                # USER stops actual inference and display, not just commands.
                model.index=1
                until(lambda:app.diagnostics.snapshot()['hands_state']=='RESTRICTED' and app.gate.candidate is None)
                assert app.canvas.itemcget(app.button_item,'state')=='hidden'
                assert app.canvas.itemcget(app.control_item,'state')=='hidden'
                calls=hands.calls;reads=camera.reads
                until(lambda:camera.reads>=reads+6)
                assert hands.calls==calls and not app.diagnostics.snapshot()['hands']
                detector.visible=False
                until(lambda:not app.diagnostics.snapshot()['faces'])
                assert not app.diagnostics.snapshot()['hands']
                # OPERATOR can track hands but cannot open desktop or admin control.
                model.index=2;detector.visible=True
                until(lambda:app.diagnostics.snapshot()['hands_state']=='TRACKED')
                assert app.gate.candidate is None and app.diagnostics.snapshot()['hands']
                app.toggle_control();assert not app.control.enabled
                app.recapturing.set()
                until(lambda:app.diagnostics.snapshot()['hands_state']=='PAUSED')
                assert not app.diagnostics.snapshot()['hands']
                app.recapturing.clear()
                until(lambda:app.diagnostics.snapshot()['hands_state']=='TRACKED')
                # SUPERVISOR runs desktop control against a harmless fake backend.
                model.index=3
                until(lambda:app.gate.candidate is not None and app.gate.candidate.role=='SUPERVISOR')
                fake=Backend();surface=Overlay()
                app.control=DesktopControl(app.launcher,backend_factory=lambda:fake,overlay_factory=lambda:surface)
                assert app.canvas.itemcget(app.control_item,'state')=='normal'
                app.toggle_control();assert app.control.enabled
                until(lambda:len(fake.cursor_moves)>0)
                detector.visible=False
                until(lambda:app.authority.current() is None,seconds=5)
                until(lambda:not app.control.enabled and app.diagnostics.snapshot()['hands_state']=='RESTRICTED')
                calls=hands.calls;reads=camera.reads
                until(lambda:camera.reads>=reads+4)
                assert hands.calls==calls
                # Recognition restores access; changing to USER revokes it immediately.
                detector.visible=True
                until(lambda:app.authority.current() is not None)
                app.toggle_control();assert app.control.enabled
                model.index=1
                until(lambda:app.authority.current() is not None and app.authority.current().role=='USER')
                until(lambda:not app.control.enabled and app.diagnostics.snapshot()['hands_state']=='RESTRICTED')
                model.index=0
                until(lambda:app.diagnostics.snapshot()['hands_state']=='TRACKED')
                for fullscreen in (True,False):
                    root.attributes('-fullscreen',fullscreen);root.update()
                    assert app.photo and abs(app.photo.width()/app.photo.height()-4/3)<.02
                assert opened.call_count==1
                assert not any(isinstance(child,tk.Toplevel) for child in root.winfo_children())
                assert all(path.read_bytes()==value for path,value in before.items())
            finally:close_app(app)
        assert camera.released and face.closed and hands.closed
        assert not app.worker.is_alive()
    print('Hands UI passed: OPERATOR+ tracking, USER/unknown stop actual inference, SUPERVISOR desktop access, USER/loss revoke it, toggles/errors, single camera/window, unchanged records, fullscreen and cleanup.')


if __name__=='__main__':main()
