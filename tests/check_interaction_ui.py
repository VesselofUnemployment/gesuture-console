"""Button-only panels and live terminal checks with temporary profiles."""
import _bootstrap  # Resolve application imports from any working directory.
import ctypes
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import tkinter as tk
from unittest.mock import patch
from person_store import save_person
from profile_store import ProfileRepository
from _ui_helpers import close_app
from recognition_ui import RecognitionWindow
from test_registration import vector
from test_vision_features import NewRecognizer
from check_vision_features_ui import Camera,Detector
from check_face_animation_ui import Tracker


def main():
    try:ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError,OSError):pass
    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'absent.npz'
        for index,(name,role) in enumerate((('Manager','ADMIN'),('Deniz','USER'),('Chief','SUPERVISOR'))):
            save_person(name,('Software',),role,[vector(index)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy)
        before={path:path.read_bytes() for path in directory.glob('*.npz')}
        root=tk.Tk();camera=Camera();tracker=Tracker();model=NewRecognizer(0)
        with patch('recognition_ui.cv2.VideoCapture',return_value=camera) as opened, \
             patch('recognition_ui.FaceTracker',return_value=tracker), \
             patch('soft_sounds.winsound.PlaySound') as audio:
            app=RecognitionWindow(root,Detector(),model,repo=repo)
            def until(condition,seconds=6):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    root.update()
                    if condition():return
                    time.sleep(.015)
                raise AssertionError('Interaction UI timeout: '+str(app.diagnostics.snapshot()))
            def pause(seconds):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    root.update();time.sleep(.015)
            try:
                until(lambda:app.gate.candidate is not None)
                app.panel_button.invoke();root.update()
                terminal=app.terminal
                assert terminal.actor_identity==repo.find('Manager').identity
                assert terminal.window.winfo_toplevel() is root and opened.call_count==1
                assert not any(isinstance(child,tk.Toplevel) for child in root.winfo_children())
                terminal.execute('watch');root.update()
                assert terminal.watching and terminal.entry.cget('state')=='readonly'
                app.feed.add('Live event reached terminal')
                until(lambda:'Live event reached terminal' in terminal.output.get('1.0','end'))
                reads=camera.reads;pause(.25);assert camera.reads>reads
                terminal.entry.focus_force()
                terminal.entry.event_generate('<Control-c>');root.update()
                assert not terminal.watching and terminal.entry.cget('state')=='normal'
                terminal.execute('vision sound off')
                assert not app.sounds.enabled
                terminal.entry.insert(0,'vision s');terminal.complete()
                assert terminal.entry.get() in ('vision status','vision sound')
                terminal.entry.delete(0,'end')
                terminal.execute('watch');app.feed.add('Must be cleared on lock')
                terminal.manual_lock();root.update()
                assert terminal.locked and not terminal.watching
                assert 'SESSION LOCKED' in terminal.output.get('1.0','end')
                app.feed.add('Must never appear after lock');pause(.2)
                assert 'Must never appear' not in terminal.output.get('1.0','end')
                terminal.close();root.update()
                # USER never gets a management panel.
                model.index=1
                until(lambda:app.gate.candidate is None)
                app.open_panel();root.update()
                assert app.terminal is None
                # SUPERVISOR gets their own panel, still with the existing ADMIN restriction.
                model.index=2
                until(lambda:app.gate.candidate is not None)
                app.panel_button.invoke();root.update()
                chief=app.terminal
                assert chief.actor_identity==repo.find('Chief').identity
                chief.execute('sudo user edit Manager name Wrong')
                assert 'only USER and OPERATOR' in chief.output.get('1.0','end')
                assert repo.find('Manager') is not None
                chief.execute('watch')
                model.index=3
                until(lambda:chief.locked,seconds=6)
                assert not chief.watching and '5 seconds' in chief.output.get('1.0','end')
                assert all(path.read_bytes()==value for path,value in before.items())
                assert audio.called  # Calls are mocked; no speaker playback in checks.
            finally:close_app(app)
        assert camera.released and tracker.closed
    print('Interaction UI passed: button-only ADMIN/SUPERVISOR panels, USER denial, live watch/Ctrl+C, mute, lock clears events, five-second timeout, single camera, unchanged face files.')


if __name__=='__main__':main()
