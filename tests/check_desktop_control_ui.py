"""Real Win32 surfaces and disposable window. Never moves a user's window."""
import sys
from pathlib import Path
import ctypes as C
from ctypes import wintypes as W
import subprocess
import os
import time
import tkinter as tk
from tempfile import TemporaryDirectory
from unittest.mock import patch
from dataclasses import replace

if '--fixture' in sys.argv:
    try:C.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:pass
    fixture=tk.Tk();fixture.title('Vision Control / Disposable Control Test')
    fixture.geometry('440x360+100+180');fixture.minsize(300,320)
    fixture.attributes('-topmost',True);fixture.update()
    native=C.WinDLL('user32');native.GetParent.restype=W.HWND;native.GetParent.argtypes=(W.HWND,)
    print(native.GetParent(fixture.winfo_id()) or fixture.winfo_id(),os.getpid(),flush=True)
    fixture.after(45000,fixture.destroy);fixture.mainloop();sys.exit(0)

import _bootstrap
from desktop_windows import WindowBackend
from desktop_control import Rect, DesktopControl, ControlPolicy
from control_overlay import ControlOverlay
from profile_store import ProfileRepository
from person_store import save_person
from test_registration import vector
from _ui_helpers import close_app
from recognition_ui import RecognitionWindow
from check_app_gestures_ui import Tray, Launcher
from test_desktop_control import Backend, Overlay


def pump(root,seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:root.update();time.sleep(.01)


def main():
    failures=[]
    old_hook=sys.unraisablehook
    sys.unraisablehook=lambda failure:failures.append(str(failure.exc_value))
    try:C.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:pass
    root=tk.Tk();root.withdraw()
    backend=WindowBackend();overlay=ControlOverlay()
    backend.user.GetForegroundWindow.restype=W.HWND
    backend.user.PostMessageW.argtypes=(W.HWND,W.UINT,W.WPARAM,W.LPARAM)
    backend.user.PostMessageW.restype=W.BOOL
    process=subprocess.Popen([sys.executable,'-B',__file__,'--fixture'],stdout=subprocess.PIPE,text=True)
    try:
        hwnd,fixture_pid=map(int,process.stdout.readline().split());pump(root,.25)
        target=backend.inspect(hwnd)
        assert target and target.pid==fixture_pid,(target,fixture_pid)
        # Only the explicitly created helper HWND is passed to any mutation API.
        moved=Rect(230,210,670,470)
        assert backend.place(target,moved);pump(root,.2)
        actual=backend.inspect(hwnd)
        assert abs(actual.rect.left-moved.left)<=2 and abs(actual.rect.top-moved.top)<=2,actual
        assert backend.pick((300,250)).hwnd==hwnd
        foreground=backend.user.GetForegroundWindow()
        view=dict(mode='wheel',point=backend.center,hover=6,pinched=False,target=None,preview=None,message='MOVE WINDOW')
        overlay.show(view,backend);pump(root,.3);overlay.show(view,backend);pump(root,.1)
        assert backend.user.GetForegroundWindow()==foreground,'Overlay stole keyboard focus'
        opened=overlay.opened;paints=overlay.wheel.paint_count
        # A single missing hand preserves the same wheel and fade state. It
        # removes the live cursor/target, so a frozen hand cannot issue actions.
        view['tracking']=False
        overlay.show(view,backend);pump(root,.1)
        assert overlay.wheel.active and not overlay.pointer.active
        assert overlay.opened==opened and overlay.wheel.paint_count==paints
        view['tracking']=True
        for index in range(20):
            view['point']=(backend.center[0]+index,backend.center[1])
            overlay.show(view,backend);root.update()
        assert overlay.wheel.paint_count==paints,'Static wheel was repainted for pointer movement'
        for surface in (overlay.wheel,overlay.pointer,overlay.outline):
            assert surface.get_style(surface.hwnd,-20)&(0x80000|0x20|0x8000000)==(0x80000|0x20|0x8000000)
        # Click-through HUD does not replace the selected app window.
        assert backend.pick((300,250)).hwnd==hwnd
        snap=Rect(backend.area.left,backend.area.top,backend.area.left+backend.area.width//2,backend.area.bottom)
        assert backend.place(actual,snap);pump(root,.2)
        actual=backend.inspect(hwnd)
        assert abs(actual.rect.width-snap.width)<=2 and abs(actual.rect.height-snap.height)<=2,actual
        # Maximized windows restore to a movable size on grab.
        backend.user.ShowWindowAsync(hwnd,3);pump(root,.25)
        maximized=backend.inspect(hwnd);assert backend.user.IsZoomed(hwnd)
        grabbed=backend.grab(maximized,(backend.center[0],backend.area.top+80));pump(root,.3)
        assert grabbed and not backend.user.IsZoomed(hwnd)
        assert backend.valid(grabbed)
        assert backend.place(grabbed,Rect(300,250,1000,670));pump(root,.2)
        # Exercise two-hand scaling against only this disposable helper window.
        original_pick=backend.pick
        backend.pick=lambda point:backend.inspect(hwnd)
        policy=ControlPolicy(backend);policy.set_enabled(True);policy.mode='move'
        def hand(number,x,y,pinch):
            area=backend.area
            return dict(number=number,position=(.16+.68*(x-area.left)/(area.width-1),
                        .14+.72*(y-area.top)/(area.height-1)),
                        resize_position=(x/1000,y/1000),pinch=pinch,open=False)
        def pair(span,pinch):
            cx,cy=backend.center
            end=time.monotonic()+.25
            while time.monotonic()<end:
                policy.update([hand(1,cx-span/2,cy,pinch),hand(2,cx+span/2,cy,pinch)],time.monotonic())
                pump(root,.02)
        pair(300,2.);pair(300,.1)
        assert policy.resize is not None
        initial=backend.inspect(hwnd).rect
        minimum,maximum=backend.resize_limits(backend.inspect(hwnd))
        assert minimum[0]>=300 and minimum[1]>=320,(minimum,maximum)
        pair(450,.1);pump(root,.15)
        enlarged=backend.inspect(hwnd).rect
        assert enlarged.width>initial.width,(initial,enlarged)
        assert abs(enlarged.width/enlarged.height-initial.width/initial.height)<.02
        pair(90,.1);pump(root,.15)
        small=backend.inspect(hwnd).rect
        assert small.height>=minimum[1] and small.width>=minimum[0],(small,minimum)
        assert abs(small.width/small.height-initial.width/initial.height)<.02,(initial,small)
        assert abs(small.width-policy.bounds.width)<=2 and abs(small.height-policy.bounds.height)<=2,(small,policy.bounds)
        pair(90,2.);pump(root,.15)
        assert policy.resize is None and policy.mode=='move'
        assert backend.inspect(hwnd).rect.width<initial.width
        backend.pick=original_pick
        # GUI object/resource count stays bounded while switching HUD states.
        kernel=C.WinDLL('kernel32');kernel.GetCurrentProcess.restype=W.HANDLE
        backend.user.GetGuiResources.argtypes=(W.HANDLE,W.DWORD);backend.user.GetGuiResources.restype=W.DWORD
        handle=kernel.GetCurrentProcess();count=backend.user.GetGuiResources(handle,0)
        for index in range(80):
            view['hover']=index%8;view['target']=actual.rect if index%2 else None
            overlay.show(view,backend);root.update()
        after=backend.user.GetGuiResources(handle,0)
        assert after<=count+3,(count,after)
        overlay.hide();assert not overlay.wheel.active and not overlay.pointer.active
        assert not failures,failures
    finally:
        overlay.close();root.destroy()
        if process.poll() is None:
            # Close only our fixture's known handle; never terminate other processes.
            if 'hwnd' in locals():backend.user.PostMessageW(hwnd,0x10,0,0)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=3)

    with TemporaryDirectory() as temporary:
        directory=Path(temporary)/'people';legacy=Path(temporary)/'none.npz'
        save_person('Manager',('Software',),'ADMIN',[vector(0)]*3,directory,legacy)
        save_person('Visitor',('Software',),'USER',[vector(1)]*3,directory,legacy)
        save_person('Supervisor',('Software',),'SUPERVISOR',[vector(2)]*3,directory,legacy)
        save_person('Operator',('Software',),'OPERATOR',[vector(3)]*3,directory,legacy)
        repo=ProfileRepository(directory,legacy)
        people={p.identity:p for p in repo.people()}
        admin=next(p for p in people.values() if p.role=='ADMIN')
        visitor=next(p for p in people.values() if p.role=='USER')
        supervisor=next(p for p in people.values() if p.role=='SUPERVISOR')
        operator=next(p for p in people.values() if p.role=='OPERATOR')
        before={p:p.read_bytes() for p in directory.glob('*.npz')}
        root=tk.Tk();root.withdraw();launcher=Launcher();fake=Backend();surface=Overlay()
        with patch('recognition_ui.AppLauncher',return_value=launcher):
            app=RecognitionWindow(root,repo=repo,start_camera=False,background=True,tray_factory=Tray)
        app.control=DesktopControl(launcher,backend_factory=lambda:fake,overlay_factory=lambda:surface)
        def frame(person,controls=(),hands=()):
            stamp=time.monotonic()
            app.accept_frame(None,[person.identity] if person else [],people,stamp,
                presentation=dict(identity=person.identity if person else None,phase='HOLD',fraction=1.,hands=hands,control_hands=controls))
        try:
            frame(visitor);app.toggle_control();assert not app.control.enabled
            assert app.canvas.itemcget(app.control_item,'state')=='hidden'
            frame(admin);assert app.canvas.itemcget(app.control_item,'state')=='normal'
            app.actions.put('control-toggle');pump(root,.08)
            assert app.control.enabled and root.state()=='withdrawn'
            # Existing numeric gestures are suppressed for the full control session.
            hand=dict(number=1,position=(.5,.5),pinch=2.,open=True)
            end=time.monotonic()+1.3
            while time.monotonic()<end:
                frame(admin,[hand],[dict(number=1,gesture='Open_Palm',fingers=5)])
                pump(root,.04)
            assert app.control.policy.mode=='wheel' and surface.visible
            assert launcher.calls==[]
            frame(None,[hand]);assert app.control.enabled,'3-second face grace was lost'
            frame(visitor,[hand]);assert not app.control.enabled,'Other recognized identity retained ADMIN control'
            frame(operator);app.toggle_control();assert not app.control.enabled
            assert app.canvas.itemcget(app.control_item,'state')=='hidden'
            frame(supervisor);app.toggle_control();assert app.control.enabled
            assert app.canvas.itemcget(app.control_item,'state')=='normal'
            frame(supervisor,[dict(hand,open=False)]);assert fake.cursor_moves
            frame(operator);pump(root,.08);assert not app.control.enabled
            frame(admin);app.toggle_control();assert app.control.enabled
            fake.escape=True;pump(root,.1);assert not app.control.enabled
            fake.escape=False;frame(admin);app.toggle_control()
            app.changed();assert not app.control.enabled
            assert all(p.read_bytes()==data for p,data in before.items())
        finally:close_app(app)
        assert launcher.closed and surface.closed
    sys.unraisablehook=old_hook
    print('Desktop control UI passed: disposable HWND resize/move/restore, no focus theft, bounded GDI, SUPERVISOR+ button/tray/control, OPERATOR/USER denial, hidden operation, numeric suppression, face grace, revision/ESC cancellation, unchanged templates and cleanup.')


if __name__=='__main__':main()
