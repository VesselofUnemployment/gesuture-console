"""SESSION START UI with synthetic hands/users and launch stubs; no real apps/UAC."""
import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import time,tkinter as tk
from types import SimpleNamespace
from _ui_helpers import close_app
from recognition_ui import RecognitionWindow
from hand_tracking import Hand
from person_store import save_person
from profile_store import ProfileRepository
from app_launcher import AppLauncher, SESSION_KEYS
from session_start import start_session
from test_registration import vector
from test_session_start import upward
from test_desktop_control import Backend,Overlay
from check_app_gestures_ui import Tray


class Notice:
    def __init__(self,root):self.messages=[]
    def observe(self,*args,**kwargs):pass
    def show_message(self,*args):self.messages.append(args)
    def hide(self):pass
    def close(self):pass


def main():
    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'none.npz'
        for index,role in enumerate(('ADMIN','SUPERVISOR','OPERATOR','USER')):
            save_person(role,('Software',),role,[vector(index)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy);people={p.identity:p for p in repo.people()}
        profiles={p.role:p for p in people.values()}
        targets={key:dict(kind='file',target=fr'C:\fake\{key}.exe') for key in ('1','2','3','4','5','fist')}
        launches=[];runs=[]
        def runner(targets,stop,pending):
            runs.append(True)
            return start_session(targets, stop, pending, snapshot=lambda: [], opener=launches.append)
        launcher = AppLauncher(targets, session_runner=runner,
                               session_targets={key: targets[key] for key in SESSION_KEYS})
        root=tk.Tk();root.withdraw()
        with patch('recognition_ui.AppLauncher',return_value=launcher),patch('recognition_ui.DesktopNotice',Notice):
            app=RecognitionWindow(root,repo=repo,start_camera=False,background=True,tray_factory=Tray)
        fake=Backend();app.control.backend_factory=lambda:fake;app.control.overlay_factory=Overlay
        person=profiles['ADMIN'];_,points=upward()
        before={p:p.read_bytes() for p in directory.glob('*.npz')}
        def pump(seconds):
            end=time.monotonic()+seconds
            while time.monotonic()<end:root.update();time.sleep(.005)
        def frames(session,seconds=1.3,multiple=False):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                stamp=time.monotonic()
                app.accept_frame(None,[person.identity] if person else [],people,stamp)
                hands=[Hand(1,points,session_start=True)] if session else []
                if multiple:hands.append(Hand(2,points+100,session_start=True))
                app.publish_hands(hands,(480,640,3),stamp,app.revision,stamp)
                pump(.04)
        try:
            frames(True);assert runs==[],'Startup held pose fired'
            frames(False,.65);frames(True,1.5)
            assert len(runs)==1 and len(launches)==5,(runs,launches)
            assert targets['4'] in launches and targets['fist'] not in launches
            assert root.state()=='withdrawn' and app.notice.messages[-1][0]=='SESSION START'
            frames(True,1.);assert len(runs)==1
            frames(False,.65);frames(True,1.3)
            assert len(runs)==2 and len(launches)==5,'Pending startups duplicated'
            for role in ('SUPERVISOR','OPERATOR','USER'):
                person=profiles[role];frames(False,.6);frames(True,1.3)
                assert len(runs)==2,role
            person=profiles['ADMIN'];frames(False,.6);app.diagnostics.set_apps_enabled(False)
            frames(True);assert len(runs)==2;app.diagnostics.set_apps_enabled(True)
            frames(False,.6);frames(True,multiple=True);assert len(runs)==2
            frames(False,.6);app.recapturing.set();frames(True);assert len(runs)==2;app.recapturing.clear()
            frames(False,.6);app.control.set_enabled(True);frames(True);assert len(runs)==2
            app.control.set_enabled(False)
            frames(False,.6);person=None;pump(3.1);frames(True);assert len(runs)==2
            assert all(p.read_bytes()==data for p,data in before.items())
        finally:close_app(app)
    print('SESSION START UI passed: real payload -> ADMIN held gesture -> async configured five-app batch, no repeat/duplicate, hidden window, partial/result UI, role/app-toggle/recapture/two-hand/CONTROL/stale-authority gates, unchanged templates and cleanup. No real app or UAC launched.')


if __name__=='__main__':main()
