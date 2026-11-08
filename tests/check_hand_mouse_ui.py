"""Real input only over our disposable button; restores cursor/focus afterward."""
import _bootstrap
import ctypes as C
from ctypes import wintypes as W
import time
import tkinter as tk
from types import SimpleNamespace
from desktop_control import DesktopControl
from desktop_windows import WindowBackend
from control_overlay import ControlOverlay


def main():
    try:C.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:pass
    backend=WindowBackend();u=backend.user
    for name,result,args in (
        ('GetCursorPos',W.BOOL,(C.POINTER(W.POINT),)),
        ('GetForegroundWindow',W.HWND,()),('SetForegroundWindow',W.BOOL,(W.HWND,)),
        ('WindowFromPoint',W.HWND,(W.POINT,)),('GetAncestor',W.HWND,(W.HWND,W.UINT)),
        ('GetParent',W.HWND,(W.HWND,))):
        fn=getattr(u,name);fn.restype=result;fn.argtypes=args
    original_cursor=W.POINT();assert u.GetCursorPos(C.byref(original_cursor))
    original_focus=u.GetForegroundWindow()
    root=tk.Tk();root.title('Vision Control / Disposable Mouse Test')
    root.geometry('480x260+140+160');root.attributes('-topmost',True)
    events=[]
    button=tk.Button(root,text='DISPOSABLE CLICK TARGET',font=('Consolas',14),
                     command=lambda:events.append('click'))
    button.pack(fill='both',expand=True,padx=70,pady=60)
    root.update()
    hwnd=u.GetParent(root.winfo_id()) or root.winfo_id()
    point=(button.winfo_rootx()+button.winfo_width()//2,
           button.winfo_rooty()+button.winfo_height()//2)
    original_click=backend.click_left
    def guarded_click(position):
        root.update()
        assert button.winfo_rootx()+8<position[0]<button.winfo_rootx()+button.winfo_width()-8,position
        assert button.winfo_rooty()+8<position[1]<button.winfo_rooty()+button.winfo_height()-8,position
        under=u.WindowFromPoint(W.POINT(*map(round,position)))
        assert u.GetAncestor(under,2)==hwnd,'Test target obscured; refusing to click'
        return original_click(position)
    backend.click_left=guarded_click
    launches=[]
    control=DesktopControl(SimpleNamespace(request=launches.append),
        backend_factory=lambda:backend,overlay_factory=ControlOverlay)
    def frame(pinch=2.,opened=False,position=point,seconds=.25):
        area=backend.mouse_area
        hand=dict(number=1,position=(.16+.68*(position[0]-area.left)/(area.width-1),
                  .14+.72*(position[1]-area.top)/(area.height-1)),pinch=pinch,open=opened)
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            control.update([hand],time.monotonic(),True)
            root.update();time.sleep(.02)
    def cursor():
        p=W.POINT();assert u.GetCursorPos(C.byref(p));return p.x,p.y
    try:
        assert control.set_enabled(True)
        frame();assert max(abs(a-b) for a,b in zip(cursor(),point))<=2,(cursor(),point)
        frame(pinch=.1,position=(point[0]+40,point[1]+20),seconds=.5)
        assert events==['click'],events
        assert max(abs(a-b) for a,b in zip(cursor(),control.policy.point))<=2
        frame();frame(pinch=.1);assert events==['click','click'],events
        frame();frame(opened=True,seconds=.6)
        assert control.policy.mode=='wheel'
        before=cursor();frame(pinch=.1)
        assert cursor()==before and len(events)==2 and launches==[]
        control.set_enabled(False)
        frame(position=(point[0]+90,point[1]+50),pinch=.1)
        assert cursor()==before and len(events)==2
        assert not backend.mouse_down
        print('Hand mouse UI passed: real pointer movement, positioned single clicks on disposable button, closing-finger aim lock, no repeat while held, wheel isolation, OFF stops input, click-through overlay, released buttons.')
    finally:
        control.close()
        # Restore only our test's focus/position, never override a user's change.
        if u.GetForegroundWindow()==hwnd and u.IsWindow(original_focus):u.SetForegroundWindow(original_focus)
        current=cursor()
        if point[0]-10<=current[0]<=point[0]+10 and point[1]-10<=current[1]<=point[1]+10:
            u.SetCursorPos(original_cursor.x,original_cursor.y)
        root.destroy()


if __name__=='__main__':main()
