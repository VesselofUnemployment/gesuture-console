"""One-window camera worker integration, using only temporary fake identities."""
import _bootstrap  # Resolve application imports from any working directory.
import ctypes
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
from check_vision_features_ui import Camera, Detector


class IdleHands:
    """Keep the face animation check independent of the separate hand model."""
    def measure(self, frame, now):return []
    def reset(self):pass
    def close(self):pass


class Tracker:
    def __init__(self):
        self.calls=0;self.closed=False;self.visible=True
        self.points=np.random.default_rng(3).uniform((105,105),(245,245),(478,2)).astype(np.float32)
    def measure(self, frame, face, now):
        self.calls+=1
        return self.points.copy() if self.visible else None
    def close(self):self.closed=True


def main():
    try:ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError,OSError):pass
    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'absent.npz'
        save_person('Manager',('Software','Mechanics'),'ADMIN',[vector(0)]*3,directory,legacy)
        save_person('Deniz',('Design',),'USER',[vector(1)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy)
        before={p:p.read_bytes() for p in directory.glob('*.npz')}
        camera=Camera();model=NewRecognizer(0);tracker=Tracker();root=tk.Tk()
        with patch('recognition_ui.cv2.VideoCapture',return_value=camera) as opened, \
             patch('recognition_ui.FaceTracker',return_value=tracker), \
             patch('recognition_ui.HandTracker',return_value=IdleHands()):
            app=RecognitionWindow(root,Detector(),model,repo=repo)
            app.sounds.set_enabled(False)
            def until(condition,seconds=5):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    root.update()
                    if condition():return
                    time.sleep(.015)
                raise AssertionError('Camera animation UI condition timed out: '+str(app.diagnostics.snapshot()))
            try:
                until(lambda:app.gate.candidate is not None)
                # Panel permission is available immediately, independent of animation.
                assert app.canvas.itemcget(app.button_item,'state')=='normal'
                until(lambda:app.diagnostics.snapshot()['animation']=='ASSEMBLING')
                app.open_panel();root.update()
                app.terminal.execute('vision status')
                assert 'FACE TRACKING' in app.terminal.output.get('1.0','end')
                app.terminal.execute('exit');root.update()
                for mode in ('small','fullscreen','restored'):
                    if mode=='small':root.geometry('640x480')
                    elif mode=='fullscreen':root.attributes('-fullscreen',True)
                    else:root.attributes('-fullscreen',False);root.geometry('960x720')
                    root.update();time.sleep(.08);root.update()
                    assert app.photo and abs(app.photo.width()/app.photo.height()-4/3)<.02
                until(lambda:app.diagnostics.snapshot()['animation']=='READY',seconds=7)
                assert tracker.calls>10
                # No extra landmark inference once the transient mesh has faded.
                reads,calls=camera.reads,tracker.calls
                until(lambda:camera.reads>=reads+8)
                assert tracker.calls==calls
                # Brief unknown frames do not replay a completed animation.
                model.index=2
                until(lambda:app.diagnostics.snapshot()['animation']=='SUSPENDED')
                reads=camera.reads
                until(lambda:camera.reads>=reads+20)
                model.index=0
                until(lambda:app.diagnostics.snapshot()['animation']=='READY')
                assert tracker.calls==calls
                # Five seconds of actual loss starts a fresh measured scan.
                model.index=2
                until(lambda:app.diagnostics.snapshot()['animation']=='SUSPENDED')
                until(lambda:app.diagnostics.snapshot()['animation']=='IDLE',seconds=6)
                model.index=0
                until(lambda:app.diagnostics.snapshot()['animation']=='ASSEMBLING')
                assert tracker.calls>calls
                # A fresh identity cancels the old animation, and USER has no panel.
                model.index=1
                until(lambda:app.diagnostics.snapshot()['animation']=='ASSEMBLING')
                until(lambda:app.canvas.itemcget(app.button_item,'state')=='hidden')
                tracker.visible=False
                until(lambda:app.diagnostics.snapshot()['tracking']=='NOT_FOUND')
                tracker.visible=True
                until(lambda:app.diagnostics.snapshot()['tracking']=='TRACKED')
                assert opened.call_count==1
                assert all(p.read_bytes()==content for p,content in before.items())
            finally:close_app(app)
        assert camera.released and tracker.closed
    print('Animation UI passed: immediate authority, real worker, same-window panel, resize/fullscreen, measured mesh, short-loss resume, five-second rescan, identity change, tracking loss, single-camera release, unchanged face records.')


if __name__=='__main__':main()
