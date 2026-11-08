"""Real native overlay: no focus theft, transparent mouse passthrough and hidden root."""
import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
import ctypes as C
from ctypes import wintypes as W
import time
import tkinter as tk
from person_store import save_person
from profile_store import ProfileRepository
from _ui_helpers import close_app
from recognition_ui import RecognitionWindow
from desktop_notice import HEADER
from test_registration import vector


class Tray:
    def __init__(self,callback):self.closed=False
    def close(self):self.closed=True
    def set_tip(self,text):pass


def main():
    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'absent.npz'
        save_person('Manager',('Software',),'ADMIN',[vector(0)]*3,directory,legacy)
        save_person('admin',('Design',),'USER',[vector(1)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy);profiles={p.identity:p for p in repo.people()}
        admin=repo.find('Manager');user=repo.find('admin')
        root=tk.Tk();root.withdraw()
        app=RecognitionWindow(root,repo=repo,start_camera=False,background=True,tray_factory=Tray)
        notice=app.notice
        native=C.WinDLL('user32',use_last_error=True)
        native.GetForegroundWindow.restype=W.HWND
        native.IsWindowVisible.argtypes=(W.HWND,)
        root.update()  # Let Tk finish its initial root setup before measuring the notice's focus.
        time.sleep(.1);root.update()
        before=native.GetForegroundWindow()
        def frame(p=admin,identities=None,stamp=None):
            app.accept_frame(None,[p.identity] if identities is None else identities,profiles,
                             time.monotonic() if stamp is None else stamp)
            root.update()
        def pump(seconds,p=admin):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                frame(p);time.sleep(.025)
        try:
            pump(.7)
            assert notice.window is not None and native.IsWindowVisible(notice.window.hwnd)
            assert root.state()=='withdrawn' and not app.visible.is_set() and app.photo is None
            assert native.GetForegroundWindow()==before, 'Recognition notice took foreground focus'
            style=notice.window.get_style(notice.window.hwnd,-20)
            required=0x80000|0x20|0x80|0x8000000
            assert style & required==required and not style & 0x40000
            assert notice.window.header==HEADER
            assert notice.window.text=='Welcome back, Manager'
            assert notice.window.opacity>.9
            assert abs(notice.window.left+notice.window.width/2-root.winfo_screenwidth()/2)<2
            assert notice.window.top==round(28*notice.window.scale)
            pump(3.85)
            assert not native.IsWindowVisible(notice.window.hwnd) and notice.job is None
            pump(.35,user)
            assert notice.window.text=='Welcome back, admin'
            assert app.gate.candidate is None and app.terminal is None
            # Multiple faces, stale frames and recapture must never claim active authority.
            frame(user,[admin.identity,user.identity])
            assert not native.IsWindowVisible(notice.window.hwnd)
            frame(user,stamp=time.monotonic()-10)
            assert notice.started is None
            app.recapturing.set();pump(.4,user);app.recapturing.clear()
            assert notice.started is None
            # No new welcome after a short loss; identity change can welcome afresh.
            pump(.4,admin)
            assert native.IsWindowVisible(notice.window.hwnd)
            root.update();time.sleep(.85);root.update()
            assert native.IsWindowVisible(notice.window.hwnd), 'Short loss should preserve the authority message'
            time.sleep(2.25);root.update()
            assert not native.IsWindowVisible(notice.window.hwnd), 'A stalled camera left an authority message visible'
            assert native.GetForegroundWindow()==before
        finally:close_app(app)
        assert notice.closed and notice.job is None and app.tray.closed
    print('Desktop notice passed: exact text/live username, fade/timeout, upper-center, hidden camera, no activation, click-through styles, USER permissions, stale/multiple/recapture guards and cleanup.')


if __name__=='__main__':main()
