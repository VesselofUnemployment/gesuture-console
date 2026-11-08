"""Short synthetic background soak; no physical camera, desktop input or apps."""
import _bootstrap
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import statistics
import time
import tkinter as tk
from unittest.mock import patch
from person_store import save_person
from profile_store import ProfileRepository
from recognition_ui import RecognitionWindow
from runtime_lifecycle import process_resources
from test_registration import vector
from test_vision_features import NewRecognizer
from check_vision_features_ui import Camera, Detector
from check_app_gestures_ui import Tray, Launcher
from check_face_animation_ui import Tracker, IdleHands
from _ui_helpers import close_app


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=30)
    args=parser.parse_args()
    with TemporaryDirectory() as folder:
        directory=Path(folder)/'people';legacy=Path(folder)/'absent.npz'
        save_person('Benchmark',('Software',),'ADMIN',[vector(0)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy)
        camera=Camera();face=Tracker();hands=IdleHands();launcher=Launcher()
        root=tk.Tk();root.withdraw()
        with patch('recognition_ui.cv2.VideoCapture',return_value=camera), \
             patch('recognition_ui.FaceTracker',return_value=face), \
             patch('recognition_ui.HandTracker',return_value=hands), \
             patch('recognition_ui.AppLauncher',return_value=launcher), \
             patch('soft_sounds.winsound.PlaySound'):
            app=RecognitionWindow(root,Detector(),NewRecognizer(0),repo=repo,background=True,tray_factory=Tray)
            samples=[];start=sample_at=time.monotonic()
            try:
                while time.monotonic()-start<args.seconds+3:
                    root.update();time.sleep(.005)
                    now=time.monotonic()
                    if now-start>=3 and now>=sample_at:
                        samples.append(process_resources());sample_at=now+1
                state=app.diagnostics.snapshot()
                assert state['state']=='ONLINE',state
                assert app.photo is None and face.calls==0
                assert launcher.calls==[]
                assert samples
                print(json.dumps(dict(seconds=args.seconds,frames=state['frames'],resources_first=samples[0],
                    resources_last=samples[-1],working_peak_mb=max(s.get('working_mb',0) for s in samples),
                    native_handle_growth={key:samples[-1].get(key,0)-samples[0].get(key,0)
                                          for key in ('gdi_handles','user_handles')},
                    latency=app.diagnostics.performance()),indent=2))
            finally:close_app(app)
            assert camera.released and not app.presence.writer.thread.is_alive()


if __name__=='__main__':main()
