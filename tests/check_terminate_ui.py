"""Full terminal-to-shutdown flow with a fake camera; no real app launches."""
import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import tkinter as tk
from unittest.mock import patch
from person_store import save_person
from profile_store import ProfileRepository
from _ui_helpers import close_app
from recognition_ui import RecognitionWindow
from test_registration import vector
from test_vision_features import NewRecognizer
from check_vision_features_ui import Camera, Detector
from check_face_animation_ui import Tracker, IdleHands
from check_app_gestures_ui import Tray, Launcher


class SlowReleaseCamera(Camera):
    def __init__(self):
        super().__init__()
        self.release_started=threading.Event()
        self.allow_release=threading.Event()
    def release(self):
        self.release_started.set()
        assert self.allow_release.wait(4),'Test did not permit camera release'
        super().release()


def main():
    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'absent.npz'
        save_person('Manager',('Software',),'ADMIN',[vector(0)]*3,directory,legacy)
        save_person('Helper',('Software',),'SUPERVISOR',[vector(1)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy)
        before={p:p.read_bytes() for p in directory.glob('*.npz')}
        camera=SlowReleaseCamera();face=Tracker();launcher=Launcher();model=NewRecognizer(0)
        root=tk.Tk();root.withdraw()
        with patch('recognition_ui.cv2.VideoCapture',return_value=camera), \
             patch('recognition_ui.FaceTracker',return_value=face), \
             patch('recognition_ui.HandTracker',return_value=IdleHands()), \
             patch('recognition_ui.AppLauncher',return_value=launcher), \
             patch('soft_sounds.winsound.PlaySound'):
            app=RecognitionWindow(root,Detector(),model,repo=repo,background=True,tray_factory=Tray)
            def until(condition,seconds=4):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    root.update()
                    if condition():return
                    time.sleep(.01)
                raise AssertionError('Termination condition timed out')
            try:
                until(lambda:app.gate.candidate is not None)
                app.show_window();app.open_panel();terminal=app.terminal
                terminal.execute('help')
                assert 'terminate connection' in terminal.output.get('1.0','end')
                terminal.entry.insert(0,'ter');terminal.complete()
                assert terminal.entry.get()=='terminate'
                terminal.entry.delete(0,'end');terminal.entry.insert(0,'terminate con');terminal.complete()
                assert terminal.entry.get()=='terminate connection'
                terminal.execute('exit')
                assert app.terminal is None and not app.closed and not app.stop.is_set()
                until(lambda:app.gate.candidate is not None)
                app.open_panel();terminal=app.terminal
                # Actual command input path, including history and Return handling.
                terminal.entry.insert(0,'terminate connection');terminal.submit()
                assert app.terminating and app.stop.is_set() and launcher.closed
                assert app.gate.owner is None and app.gate.candidate is None
                assert terminal.entry.cget('state')=='disabled'
                assert app.canvas.itemcget(app.button_item,'state')=='hidden'
                until(camera.release_started.is_set)
                # UI timers continue while the worker is releasing the camera.
                ticks=[];root.after(40,lambda:ticks.append(True))
                until(lambda:bool(ticks))
                output=terminal.output.get('1.0','end')
                assert 'Releasing camera...' in output and 'Authority offline.' not in output
                terminal.execute('user Manager disable')
                assert repo.find('Manager').active
                camera.allow_release.set()
                until(lambda:app.termination_stage==2)
                assert camera.released and not app.worker.is_alive()
                output=terminal.output.get('1.0','end')
                a,b,c=(output.index(text) for text in ('Releasing camera...','Authority offline.','Connection terminated.'))
                assert a<b<c and not app.closed
                until(lambda:app.closed)
                assert app.tray.closed and app.notice.closed and terminal.closed
                assert app.termination_job is None and face.closed
                assert launcher.calls==[]
                assert all(p.read_bytes()==value for p,value in before.items())
            finally:
                camera.allow_release.set()
                if not app.closed:close_app(app)
        # Command denied to SUPERVISOR without setting the application stop flag.
        root=tk.Tk();root.withdraw()
        app=RecognitionWindow(root,repo=repo,start_camera=False)
        try:
            app.sounds.set_enabled(False)
            app.gate.observe(repo.find('Helper'));app.open_panel();terminal=app.terminal
            terminal.execute('terminate connection')
            assert 'Only ADMIN' in terminal.output.get('1.0','end')
            assert not app.stop.is_set() and not app.terminating
        finally:close_app(app)
    print('Terminate UI passed: help/Tab, exit preserves camera, Return dispatch, authority/actions stop, ordered visible lines after camera release, responsive UI, full process cleanup, protected records and SUPERVISOR denial.')


if __name__=='__main__':main()
