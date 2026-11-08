"""Exercise the worker, terminal, and recapture with a fake camera."""
import _bootstrap  # Resolve application imports from any working directory.
import ctypes
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
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


class Camera:
    def __init__(self):
        self.reads=0;self.released=False
        self.frame=np.random.default_rng(0).integers(70,180,(480,640,3),dtype=np.uint8)
    def isOpened(self):return True
    def set(self,*args):pass
    def read(self):
        time.sleep(.02);self.reads+=1
        return True,self.frame.copy()
    def release(self):self.released=True


class Detector:
    def setInputSize(self,*args):pass
    def detect(self,frame):
        face=np.zeros((1,15),np.float32);face[0,:4]=(100,100,150,150)
        return None,face


def main():
    try:ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError,OSError):pass
    with TemporaryDirectory() as tmp:
        directory,legacy=Path(tmp)/'people',Path(tmp)/'absent.npz'
        save_person('Manager',('Software',),'ADMIN',[vector(0)]*3,directory,legacy)
        save_person('Deniz',('Design',),'USER',[vector(1)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy);actor=repo.find('Manager')
        model=NewRecognizer(0);camera=Camera();root=tk.Tk()
        with patch('recognition_ui.cv2.VideoCapture',return_value=camera) as opened:
            app=RecognitionWindow(root,Detector(),model,repo=repo)
            def until(condition,seconds=4):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    root.update()
                    if condition():return
                    time.sleep(.025)
                raise AssertionError('UI condition timed out')
            try:
                until(lambda:app.gate.candidate is not None)
                app.open_panel();root.update();terminal=app.terminal
                terminal.execute('vision debug on')
                until(lambda:'FPS' in app.canvas.itemcget(app.debug_item,'text'))
                terminal.execute('vision status');terminal.execute('user Manager stats')
                output=terminal.output.get('1.0','end')
                assert 'PROCESSED FPS' in output and 'TOTAL LOGINS' in output
                terminal.entry.insert(0,'vision d');terminal.complete()
                assert terminal.entry.get()=='vision debug'
                terminal.entry.delete(0,'end')
                terminal.execute('user Deniz recapture');root.update()
                view=app.recapture
                assert view and view.window.winfo_toplevel() is root
                assert not any(isinstance(child,tk.Toplevel) for child in root.winfo_children())
                model.index=1
                until(lambda:view.last_frame is not None)
                for mode in ('small','maximized','fullscreen','restored'):
                    if mode=='small':root.geometry('640x480')
                    elif mode=='maximized':root.state('zoomed')
                    elif mode=='fullscreen':root.attributes('-fullscreen',True)
                    else:
                        root.attributes('-fullscreen',False);root.state('normal');root.geometry('960x720')
                    root.update();time.sleep(.1);root.update()
                    assert view.capture_button.winfo_ismapped() and view.save_button.winfo_ismapped()
                    for widget in (view.capture_button,view.save_button,view.status):
                        y=widget.winfo_rooty()-view.window.winfo_rooty()
                        assert 0<=y and y+widget.winfo_height()<=view.window.winfo_height()
                    assert abs(view.photo.width()/view.photo.height()-4/3)<.02
                import os
                screenshot=os.environ.get('VISION_RECAPTURE_SCREENSHOT')
                if screenshot:
                    from PIL import ImageGrab
                    time.sleep(.2);root.update();ctypes.windll.dwmapi.DwmFlush()
                    hwnd=ctypes.windll.user32.GetAncestor(root.winfo_id(),2)
                    ImageGrab.grab(window=hwnd).save(screenshot)
                before=repo._source(repo.find('Deniz').identity).read_bytes()
                reads=camera.reads
                for index in range(3):
                    until(lambda:not view.busy)
                    view.capture_button.invoke()
                    until(lambda:view.session.snapshot()[0]==index+1 and not view.busy)
                assert camera.reads>reads and opened.call_count==1
                assert repo._source(repo.find('Deniz').identity).read_bytes()==before
                view.save_button.invoke();root.update()
                assert app.recapture is view and 'manager' in view.status.cget('text').lower()
                model.index=0
                until(lambda:app.gate.candidate is not None and app.gate.candidate.identity==actor.identity)
                view.save_button.invoke();root.update()
                assert app.recapture is None and not terminal.locked
                assert repo.find('Deniz').revision==1
                assert terminal.window.winfo_ismapped()
                assert len(list((Path(tmp)/'backups').rglob('*.npz')))==1
                terminal.execute('vision debug off')
                until(lambda:not app.canvas.itemcget(app.debug_item,'text'))
                terminal.execute('user Manager recapture');root.update()
                assert app.recapture is not None
                app.escape();root.update()
                assert app.recapture is None and app.terminal is terminal
                assert opened.call_count==1
            finally:close_app(app)
        assert camera.released
    print('Shared-camera UI passed: live worker/debug/stats, same-window recapture, resizing, capture acknowledgements, owner reauthentication, save and cancel.')


if __name__=='__main__':main()
