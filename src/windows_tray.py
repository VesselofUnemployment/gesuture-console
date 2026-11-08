"""Small native Windows tray icon, driven by Tk's message loop on the same thread."""
import ctypes as C
from ctypes import wintypes as W
import os
import uuid
from project_paths import PROJECT_ROOT

LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
CALLBACK = 0x8000+77
OPEN, HIDE, QUIT = 1001,1002,1003
APPS = 1004
CONTROL = 1005
FACE_DIAGNOSTICS = 1006  # Local read-only diagnostic request; absent from the menu.


class WNDCLASS(C.Structure):
    _fields_ = [('style',W.UINT),('lpfnWndProc',WNDPROC),('cbClsExtra',C.c_int),
                ('cbWndExtra',C.c_int),('hInstance',W.HINSTANCE),('hIcon',W.HICON),
                ('hCursor',W.HANDLE),('hbrBackground',W.HBRUSH),
                ('lpszMenuName',W.LPCWSTR),('lpszClassName',W.LPCWSTR)]


class NOTIFYICONDATA(C.Structure):
    _fields_ = [('cbSize',W.DWORD),('hWnd',W.HWND),('uID',W.UINT),('uFlags',W.UINT),
                ('uCallbackMessage',W.UINT),('hIcon',W.HICON),('szTip',W.WCHAR*128),
                ('dwState',W.DWORD),('dwStateMask',W.DWORD),('szInfo',W.WCHAR*256),
                ('uVersion',W.UINT),('szInfoTitle',W.WCHAR*64),('dwInfoFlags',W.DWORD),
                ('guidItem',C.c_byte*16),('hBalloonIcon',W.HICON)]


def signature(library, name, result, *arguments):
    function=getattr(library,name)
    function.restype, function.argtypes=result, arguments
    return function


class NativeTray:
    def __init__(self, on_action):
        self.on_action=on_action
        self.apps_enabled=True
        self.control_enabled=False
        self.user=C.WinDLL('user32',use_last_error=True)
        self.shell=C.WinDLL('shell32',use_last_error=True)
        self.kernel=C.WinDLL('kernel32',use_last_error=True)
        signature(self.user,'RegisterClassW',W.WORD,C.POINTER(WNDCLASS))
        signature(self.user,'UnregisterClassW',W.BOOL,W.LPCWSTR,W.HINSTANCE)
        signature(self.user,'CreateWindowExW',W.HWND,W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,
                  C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID)
        signature(self.user,'DefWindowProcW',LRESULT,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        signature(self.user,'DestroyWindow',W.BOOL,W.HWND)
        signature(self.user,'RegisterWindowMessageW',W.UINT,W.LPCWSTR)
        signature(self.user,'LoadImageW',W.HANDLE,W.HINSTANCE,W.LPCWSTR,W.UINT,C.c_int,C.c_int,W.UINT)
        signature(self.user,'LoadIconW',W.HICON,W.HINSTANCE,W.LPCWSTR)
        signature(self.user,'DestroyIcon',W.BOOL,W.HICON)
        signature(self.user,'CreatePopupMenu',W.HMENU)
        signature(self.user,'AppendMenuW',W.BOOL,W.HMENU,W.UINT,C.c_size_t,W.LPCWSTR)
        signature(self.user,'TrackPopupMenu',W.UINT,W.HMENU,W.UINT,C.c_int,C.c_int,C.c_int,W.HWND,W.LPVOID)
        signature(self.user,'DestroyMenu',W.BOOL,W.HMENU)
        signature(self.user,'GetCursorPos',W.BOOL,C.POINTER(W.POINT))
        signature(self.user,'SetForegroundWindow',W.BOOL,W.HWND)
        signature(self.user,'PostMessageW',W.BOOL,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        signature(self.kernel,'GetModuleHandleW',W.HMODULE,W.LPCWSTR)
        signature(self.shell,'Shell_NotifyIconW',W.BOOL,W.DWORD,C.POINTER(NOTIFYICONDATA))
        self.module=self.kernel.GetModuleHandleW(None)
        self.class_name='VisionControlTray-'+str(os.getpid())+'-'+uuid.uuid4().hex
        self.taskbar_message=self.user.RegisterWindowMessageW('TaskbarCreated')
        self.hwnd=self.icon=None
        self.own_icon=False
        self.registered=False
        self.added=False
        self.callback=WNDPROC(self.window_proc)  # Keep alive until the native window is destroyed.
        window_class=WNDCLASS()
        window_class.lpfnWndProc=self.callback
        window_class.hInstance=self.module
        window_class.lpszClassName=self.class_name
        try:
            if not self.user.RegisterClassW(C.byref(window_class)):raise C.WinError(C.get_last_error())
            self.registered=True
            self.hwnd=self.user.CreateWindowExW(0,self.class_name,'Vision Control Tray',0,0,0,0,0,None,None,self.module,None)
            if not self.hwnd:raise C.WinError(C.get_last_error())
            self.icon=self.user.LoadImageW(None,str(PROJECT_ROOT/'assets'/'vision.ico'),1,32,32,0x10)
            self.own_icon=bool(self.icon)
            if not self.icon:self.icon=self.user.LoadIconW(None,C.cast(C.c_void_p(32512),W.LPCWSTR))
            self.data=NOTIFYICONDATA()
            self.data.cbSize=C.sizeof(self.data)
            self.data.hWnd,self.data.uID=self.hwnd,1
            self.data.uFlags,self.data.uCallbackMessage=1|2|4|0x80,CALLBACK
            self.data.hIcon=self.icon
            self.data.szTip='Vision Control / Face + hands active'
            if not self.add():raise OSError('Windows tray icon could not be added.')
        except Exception:
            self.close()
            raise

    def add(self):
        self.added=bool(self.shell.Shell_NotifyIconW(0,C.byref(self.data)))
        if self.added:
            self.data.uVersion=4
            self.shell.Shell_NotifyIconW(4,C.byref(self.data))
        return self.added

    def set_tip(self, text):
        text=text[:127]
        if self.data.szTip==text:return
        self.data.szTip=text
        self.shell.Shell_NotifyIconW(1,C.byref(self.data))

    def window_proc(self, hwnd, message, wparam, lparam):
        if message==self.taskbar_message and getattr(self,'data',None) is not None:
            self.add()
            return 0
        if message==CALLBACK:
            event=lparam & 0xffff
            if event in (0x400,0x401,0x203):self.on_action('show')
            elif event in (0x7b,0x205):self.menu()
            return 0
        if message==0x111:
            self.action(wparam & 0xffff)
            return 0
        return self.user.DefWindowProcW(hwnd,message,wparam,lparam)

    def action(self, command):
        action={OPEN:'show',HIDE:'hide',QUIT:'quit',APPS:'apps-toggle',CONTROL:'control-toggle',FACE_DIAGNOSTICS:'face-diagnostics'}.get(command)
        if action:self.on_action(action)

    def menu(self):
        menu=self.user.CreatePopupMenu()
        if not menu:return
        try:
            for identifier,label in ((OPEN,'Open Vision Control'),(HIDE,'Hide to background')):
                self.user.AppendMenuW(menu,0,identifier,label)
            self.user.AppendMenuW(menu,0x800,0,None)
            self.user.AppendMenuW(menu,8 if self.apps_enabled else 0,APPS,'Gesture app control')
            self.user.AppendMenuW(menu,8 if self.control_enabled else 0,CONTROL,'CONTROL MODE / hand desktop')
            self.user.AppendMenuW(menu,0x800,0,None)
            self.user.AppendMenuW(menu,0,QUIT,'Quit / release camera')
            point=W.POINT()
            self.user.GetCursorPos(C.byref(point))
            self.user.SetForegroundWindow(self.hwnd)
            command=self.user.TrackPopupMenu(menu,0x100|0x2,point.x,point.y,0,self.hwnd,None)
            self.action(command)
            self.user.PostMessageW(self.hwnd,0,0,0)
        finally:self.user.DestroyMenu(menu)

    def close(self):
        if self.added:
            self.shell.Shell_NotifyIconW(2,C.byref(self.data))
            self.added=False
        if self.hwnd:
            self.user.DestroyWindow(self.hwnd)
            self.hwnd=None
        if self.own_icon and self.icon:
            self.user.DestroyIcon(self.icon)
            self.icon=None
        if self.registered:
            self.user.UnregisterClassW(self.class_name,self.module)
            self.registered=False
