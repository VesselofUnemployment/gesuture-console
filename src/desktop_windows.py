"""Windows mouse/window controls and fixed media/desktop shortcuts; no shell.

Uses async SetWindowPos without changing focus/z order. Unresponsive, desktop,
taskbar, tool and our own windows are excluded from selection.
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos
"""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import os
from desktop_control import Rect, proportional_size
from native_notice_window import MONITORINFO
from windows_tray import signature


class MOUSEINPUT(C.Structure):
    _fields_=[('dx',W.LONG),('dy',W.LONG),('mouseData',W.DWORD),
              ('dwFlags',W.DWORD),('time',W.DWORD),('dwExtraInfo',C.c_size_t)]


class KEYBDINPUT(C.Structure):
    _fields_=[('wVk',W.WORD),('wScan',W.WORD),('dwFlags',W.DWORD),
              ('time',W.DWORD),('dwExtraInfo',C.c_size_t)]


class INPUTDATA(C.Union):
    # MOUSEINPUT is the largest INPUT union member on both Win32 and Win64.
    _fields_=[('mi',MOUSEINPUT),('ki',KEYBDINPUT)]


class INPUT(C.Structure):
    _anonymous_=('data',)
    _fields_=[('type',W.DWORD),('data',INPUTDATA)]


class WINDOWPLACEMENT(C.Structure):
    _fields_=[('length',W.UINT),('flags',W.UINT),('showCmd',W.UINT),
              ('ptMinPosition',W.POINT),('ptMaxPosition',W.POINT),('rcNormalPosition',W.RECT)]


class MINMAXINFO(C.Structure):
    _fields_=[('ptReserved',W.POINT),('ptMaxSize',W.POINT),('ptMaxPosition',W.POINT),
              ('ptMinTrackSize',W.POINT),('ptMaxTrackSize',W.POINT)]


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    pid: int
    title: str
    rect: Rect
    class_name: str
    resizable: bool


class WindowBackend:
    def __init__(self):
        self.user=C.WinDLL('user32',use_last_error=True)
        self.dwm=C.WinDLL('dwmapi',use_last_error=True)
        signature(self.user,'EnumWindows',W.BOOL,C.c_void_p,W.LPARAM)
        signature(self.user,'IsWindow',W.BOOL,W.HWND)
        signature(self.user,'IsWindowVisible',W.BOOL,W.HWND)
        signature(self.user,'IsIconic',W.BOOL,W.HWND)
        signature(self.user,'IsZoomed',W.BOOL,W.HWND)
        signature(self.user,'IsHungAppWindow',W.BOOL,W.HWND)
        signature(self.user,'GetWindowRect',W.BOOL,W.HWND,C.POINTER(W.RECT))
        signature(self.user,'GetClassNameW',C.c_int,W.HWND,W.LPWSTR,C.c_int)
        signature(self.user,'GetWindowTextW',C.c_int,W.HWND,W.LPWSTR,C.c_int)
        signature(self.user,'GetWindowThreadProcessId',W.DWORD,W.HWND,C.POINTER(W.DWORD))
        signature(self.user,'MonitorFromPoint',W.HANDLE,W.POINT,W.DWORD)
        signature(self.user,'MonitorFromWindow',W.HANDLE,W.HWND,W.DWORD)
        signature(self.user,'GetMonitorInfoW',W.BOOL,W.HANDLE,C.POINTER(MONITORINFO))
        signature(self.user,'GetWindowPlacement',W.BOOL,W.HWND,C.POINTER(WINDOWPLACEMENT))
        signature(self.user,'ShowWindowAsync',W.BOOL,W.HWND,C.c_int)
        signature(self.user,'SetWindowPos',W.BOOL,W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT)
        signature(self.user,'GetAsyncKeyState',C.c_short,C.c_int)
        signature(self.user,'SetCursorPos',W.BOOL,C.c_int,C.c_int)
        signature(self.user,'SendInput',W.UINT,W.UINT,C.POINTER(INPUT),C.c_int)
        signature(self.user,'GetDpiForSystem',W.UINT)
        signature(self.user,'GetSystemMetrics',C.c_int,C.c_int)
        signature(self.user,'SendMessageTimeoutW',C.c_ssize_t,W.HWND,W.UINT,W.WPARAM,
                  W.LPARAM,W.UINT,W.UINT,C.POINTER(C.c_size_t))
        signature(self.dwm,'DwmGetWindowAttribute',C.c_long,W.HWND,W.DWORD,W.LPVOID,W.DWORD)
        suffix='PtrW' if C.sizeof(C.c_void_p)==8 else 'W'
        self.style=signature(self.user,'GetWindowLong'+suffix,C.c_ssize_t,W.HWND,C.c_int)
        self.scale=max(1.,self.user.GetDpiForSystem()/96.)
        self.excluded_pid=os.getpid()
        self.mouse_down=False
        self.pending_keys=[]
        self.refresh_area()

    def refresh_area(self):
        monitor=self.user.MonitorFromPoint(W.POINT(0,0),1)
        info=MONITORINFO();info.cbSize=C.sizeof(info)
        if not self.user.GetMonitorInfoW(monitor,C.byref(info)):raise C.WinError(C.get_last_error())
        r=info.rcWork
        self.area=Rect(r.left,r.top,r.right,r.bottom)
        m=info.rcMonitor
        self.mouse_area=Rect(m.left,m.top,m.right,m.bottom)
        self.center=(r.left+self.area.width/2,r.top+self.area.height/2)
        self.radius=min(210*self.scale,self.area.width*.29,self.area.height*.29)

    def escape_pressed(self):return bool(self.user.GetAsyncKeyState(0x1b)&0x8000)

    def move_cursor(self, point):
        if not self.release_mouse():return False
        return bool(self.user.SetCursorPos(*map(round,point)))

    def click_left(self, point):
        """One positioned click in a single input batch; never a drag/hold.

        https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
        """
        if not self.release_mouse():return False
        # Do not release or interfere with a physically held mouse button.
        if self.user.GetAsyncKeyState(0x01)&0x8000:return True
        x0,y0=(self.user.GetSystemMetrics(i) for i in (76,77))
        width,height=(self.user.GetSystemMetrics(i) for i in (78,79))
        if width<2 or height<2:return False
        x=round(max(0.,min(1.,(point[0]-x0)/(width-1)))*65535)
        y=round(max(0.,min(1.,(point[1]-y0)/(height-1)))*65535)
        inputs=(INPUT*3)()
        inputs[0].mi=MOUSEINPUT(x,y,0,0x8000|0x4000|0x0001,0,0)
        inputs[1].mi.dwFlags=0x0002
        inputs[2].mi.dwFlags=0x0004
        sent=self.user.SendInput(3,inputs,C.sizeof(INPUT))
        if sent==2:
            self.mouse_down=True;self.release_mouse()
        return sent==3

    def release_mouse(self):
        keyboard=self.release_keyboard()
        if not keyboard:return False
        if not self.mouse_down:return True
        release=INPUT();release.mi.dwFlags=0x0004
        if self.user.SendInput(1,C.byref(release),C.sizeof(INPUT))==1:
            self.mouse_down=False
        return not self.mouse_down

    @staticmethod
    def key_input(key, up=False):
        event=INPUT();event.type=1
        event.ki=KEYBDINPUT(key,0,(2 if up else 0)|(1 if key in (0x25,0x27,0x5b) else 0),0,0)
        return event

    def release_keyboard(self):
        # Retry only keys from an incomplete batch, never physical user keys.
        keys=getattr(self,'pending_keys',[])
        if not keys:return True
        events=(INPUT*len(keys))(*(self.key_input(key,True) for key in reversed(keys)))
        sent=self.user.SendInput(len(events),events,C.sizeof(INPUT))
        self.pending_keys=keys[:max(0,len(keys)-sent)]
        return not self.pending_keys

    def command(self, action):
        """Fixed, balanced input batch; one action per confirmed pinch.

        https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput
        """
        commands={'desk-left':(0x5b,0x11,0x25),'desk-right':(0x5b,0x11,0x27),
                  'volume-up':(0xaf,),'volume-down':(0xae,),'volume-mute':(0xad,),
                  'media-play':(0xb3,),'media-next':(0xb0,),'media-prev':(0xb1,)}
        keys=commands.get(action)
        if keys is None or not self.release_mouse():return False
        # Do not combine an injected shortcut with held physical modifiers.
        if any(self.user.GetAsyncKeyState(key)&0x8000 for key in set(keys)|(set((0x10,0x11,0x12,0x5b,0x5c)))):
            return False
        events=(INPUT*(len(keys)*2))(*(self.key_input(k) for k in keys),
                    *(self.key_input(k,True) for k in reversed(keys)))
        sent=self.user.SendInput(len(events),events,C.sizeof(INPUT))
        pending=[]
        for event in events[:sent]:
            key=event.ki.wVk
            if event.ki.dwFlags&2:
                if key in pending:pending.remove(key)
            else:pending.append(key)
        self.pending_keys=pending
        if pending:self.release_keyboard()
        return sent==len(events)

    def inspect(self, hwnd):
        if not self.user.IsWindow(hwnd) or not self.user.IsWindowVisible(hwnd):return None
        if self.user.IsIconic(hwnd) or self.user.IsHungAppWindow(hwnd):return None
        pid=W.DWORD();self.user.GetWindowThreadProcessId(hwnd,C.byref(pid))
        if pid.value==self.excluded_pid:return None
        style,extended=self.style(hwnd,-16),self.style(hwnd,-20)
        if style&0x40000000 or extended&(0x80|0x8000000):return None
        class_name=C.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd,class_name,256)
        if class_name.value in ('Progman','WorkerW','Shell_TrayWnd','Shell_SecondaryTrayWnd'):return None
        cloaked=W.DWORD()
        if self.dwm.DwmGetWindowAttribute(hwnd,14,C.byref(cloaked),C.sizeof(cloaked))==0 and cloaked.value:return None
        rectangle=W.RECT()
        if not self.user.GetWindowRect(hwnd,C.byref(rectangle)):return None
        bounds=Rect(rectangle.left,rectangle.top,rectangle.right,rectangle.bottom)
        if bounds.width<80 or bounds.height<40:return None
        title=C.create_unicode_buffer(256);self.user.GetWindowTextW(hwnd,title,256)
        if not title.value:return None
        return WindowTarget(hwnd,pid.value,title.value,bounds,class_name.value,bool(style&0x40000))

    def pick(self, point):
        selected=[]
        @C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        def visit(hwnd, data):
            target=self.inspect(hwnd)
            if target and target.rect.left<=point[0]<target.rect.right and target.rect.top<=point[1]<target.rect.bottom:
                selected.append(target);return False
            return True
        self.user.EnumWindows(visit,0)
        return selected[0] if selected else None

    def valid(self, target):
        current=self.inspect(target.hwnd)
        return current is not None and current.pid==target.pid and current.class_name==target.class_name

    def place(self, target, rectangle):
        if not self.valid(target):return False
        # Fixed-size windows can move, but are never forced into a half-screen size.
        sizing=rectangle.width!=target.rect.width or rectangle.height!=target.rect.height
        if sizing and not target.resizable:return False
        return bool(self.user.SetWindowPos(target.hwnd,None,rectangle.left,rectangle.top,
                    rectangle.width,rectangle.height,0x4000|0x10|0x4|0x200))

    def resize_limits(self, target):
        """Outer-window constraints; bounded query once per resize/restore.

        WM_GETMINMAXINFO is a marshalled system message. The timeout prevents
        a slow foreign app from blocking hand tracking indefinitely.
        https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-getminmaxinfo
        """
        maximum=(self.area.width,self.area.height)
        fallback=((max(200*self.scale,target.rect.width),
                   max(120*self.scale,target.rect.height)),maximum)
        if not self.valid(target):return fallback
        info=MINMAXINFO()
        info.ptMaxSize=W.POINT(*maximum)
        info.ptMaxPosition=W.POINT(self.area.left,self.area.top)
        info.ptMinTrackSize=W.POINT(self.user.GetSystemMetrics(34),self.user.GetSystemMetrics(35))
        info.ptMaxTrackSize=W.POINT(self.user.GetSystemMetrics(59),self.user.GetSystemMetrics(60))
        result=C.c_size_t()
        if not self.user.SendMessageTimeoutW(target.hwnd,0x24,0,C.addressof(info),
                                            0x1|0x2|0x20,30,C.byref(result)):
            return fallback
        minimum=(max(200*self.scale,info.ptMinTrackSize.x),
                 max(120*self.scale,info.ptMinTrackSize.y))
        maximum=tuple(min(limit,reported) if reported>0 else limit for limit,reported in
                      zip(maximum,(info.ptMaxTrackSize.x,info.ptMaxTrackSize.y)))
        return minimum,maximum

    def grab(self, target, point):
        if not self.valid(target):return None
        if not self.user.IsZoomed(target.hwnd):return self.inspect(target.hwnd)
        placement=WINDOWPLACEMENT();placement.length=C.sizeof(placement)
        if not self.user.GetWindowPlacement(target.hwnd,C.byref(placement)):return None
        info=MONITORINFO();info.cbSize=C.sizeof(info)
        monitor=self.user.MonitorFromWindow(target.hwnd,2)
        if not self.user.GetMonitorInfoW(monitor,C.byref(info)):return None
        normal=placement.rcNormalPosition
        normal_bounds=Rect(normal.left,normal.top,normal.right,normal.bottom)
        if normal_bounds.width<=0 or normal_bounds.height<=0:return None
        size=proportional_size(normal_bounds,1.,*self.resize_limits(target))
        if size is None:return None
        width,height=size
        fraction=max(0.,min(1.,(point[0]-target.rect.left)/target.rect.width))
        left=round(point[0]-fraction*width)
        top=max(self.area.top,min(self.area.bottom-height,round(point[1]-30*self.scale)))
        rectangle=Rect(left,top,left+width,top+height)
        if not self.user.ShowWindowAsync(target.hwnd,4):return None
        restored=WindowTarget(target.hwnd,target.pid,target.title,rectangle,target.class_name,target.resizable)
        if not self.place(restored,rectangle):return None
        return restored
