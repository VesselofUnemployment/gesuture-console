"""Native text-only Windows overlay; Tk's existing loop dispatches its paint messages."""
import ctypes as C
from ctypes import wintypes as W
import os
import uuid
from windows_tray import WNDCLASS, WNDPROC, LRESULT, signature


def rgb(red,green,blue):return red | green<<8 | blue<<16


class PAINTSTRUCT(C.Structure):
    _fields_=[('hdc',W.HDC),('fErase',W.BOOL),('rcPaint',W.RECT),
              ('fRestore',W.BOOL),('fIncUpdate',W.BOOL),('rgbReserved',C.c_byte*32)]


class MONITORINFO(C.Structure):
    _fields_=[('cbSize',W.DWORD),('rcMonitor',W.RECT),('rcWork',W.RECT),('dwFlags',W.DWORD)]


class NativeNoticeWindow:
    def __init__(self, header):
        self.header,self.text=header,''
        self.user=C.WinDLL('user32',use_last_error=True)
        self.gdi=C.WinDLL('gdi32',use_last_error=True)
        self.kernel=C.WinDLL('kernel32',use_last_error=True)
        signature(self.user,'RegisterClassW',W.WORD,C.POINTER(WNDCLASS))
        signature(self.user,'UnregisterClassW',W.BOOL,W.LPCWSTR,W.HINSTANCE)
        signature(self.user,'CreateWindowExW',W.HWND,W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,
                  C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID)
        signature(self.user,'DefWindowProcW',LRESULT,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        signature(self.user,'DestroyWindow',W.BOOL,W.HWND)
        signature(self.user,'ShowWindow',W.BOOL,W.HWND,C.c_int)
        signature(self.user,'SetWindowPos',W.BOOL,W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT)
        signature(self.user,'SetLayeredWindowAttributes',W.BOOL,W.HWND,W.DWORD,C.c_byte,W.DWORD)
        signature(self.user,'BeginPaint',W.HDC,W.HWND,C.POINTER(PAINTSTRUCT))
        signature(self.user,'EndPaint',W.BOOL,W.HWND,C.POINTER(PAINTSTRUCT))
        signature(self.user,'GetClientRect',W.BOOL,W.HWND,C.POINTER(W.RECT))
        signature(self.user,'FillRect',C.c_int,W.HDC,C.POINTER(W.RECT),W.HBRUSH)
        signature(self.user,'DrawTextW',C.c_int,W.HDC,W.LPCWSTR,C.c_int,C.POINTER(W.RECT),W.UINT)
        signature(self.user,'InvalidateRect',W.BOOL,W.HWND,W.LPVOID,W.BOOL)
        signature(self.user,'MonitorFromPoint',W.HANDLE,W.POINT,W.DWORD)
        signature(self.user,'GetMonitorInfoW',W.BOOL,W.HANDLE,C.POINTER(MONITORINFO))
        signature(self.user,'GetDpiForSystem',W.UINT)
        signature(self.kernel,'GetModuleHandleW',W.HMODULE,W.LPCWSTR)
        signature(self.gdi,'CreateSolidBrush',W.HBRUSH,W.DWORD)
        signature(self.gdi,'CreateFontW',W.HANDLE,*([C.c_int]*5),*([W.DWORD]*8),W.LPCWSTR)
        signature(self.gdi,'SelectObject',W.HANDLE,W.HDC,W.HANDLE)
        signature(self.gdi,'DeleteObject',W.BOOL,W.HANDLE)
        signature(self.gdi,'SetBkMode',C.c_int,W.HDC,C.c_int)
        signature(self.gdi,'SetTextColor',W.DWORD,W.HDC,W.DWORD)
        suffix='PtrW' if C.sizeof(C.c_void_p)==8 else 'W'
        self.get_style=signature(self.user,'GetWindowLong'+suffix,C.c_ssize_t,W.HWND,C.c_int)
        self.scale=max(1.,self.user.GetDpiForSystem()/96.)
        self.module=self.kernel.GetModuleHandleW(None)
        self.class_name=f'VisionControlNotice-{os.getpid()}-{uuid.uuid4().hex}'
        self.hwnd=None;self.registered=False;self.fonts=[];self.brush=None
        self.callback=WNDPROC(self.window_proc)
        definition=WNDCLASS();definition.lpfnWndProc=self.callback
        definition.hInstance=self.module;definition.lpszClassName=self.class_name
        try:
            if not self.user.RegisterClassW(C.byref(definition)):raise C.WinError(C.get_last_error())
            self.registered=True
            # Popup, topmost, layered, mouse transparent, tool window, no activation. No owner to activate.
            self.hwnd=self.user.CreateWindowExW(0x80000|0x20|0x80|0x8000000|0x8,
                self.class_name,'Vision Control Recognition Notice',0x80000000,0,0,1,1,None,None,self.module,None)
            if not self.hwnd:raise C.WinError(C.get_last_error())
            self.brush=self.gdi.CreateSolidBrush(rgb(1,2,3))
            for size,weight in ((20,600),(17,400)):
                font=self.gdi.CreateFontW(-round(size*self.scale),0,0,0,weight,0,0,0,1,0,0,4,0,'Segoe UI')
                if not font:raise C.WinError(C.get_last_error())
                self.fonts.append(font)
            if not self.brush:raise C.WinError(C.get_last_error())
        except Exception:
            self.close();raise

    def position(self):
        info=MONITORINFO();info.cbSize=C.sizeof(info)
        monitor=self.user.MonitorFromPoint(W.POINT(0,0),1)
        if not self.user.GetMonitorInfoW(monitor,C.byref(info)):raise C.WinError(C.get_last_error())
        bounds=info.rcMonitor
        self.width=min(round(640*self.scale),bounds.right-bounds.left-24)
        self.height=round(100*self.scale)
        self.left=bounds.left+(bounds.right-bounds.left-self.width)//2
        self.top=bounds.top+round(28*self.scale)

    def show(self, text):
        self.text=text
        self.position()
        self.update(0.,7.)
        self.user.InvalidateRect(self.hwnd,None,False)
        self.user.ShowWindow(self.hwnd,4)  # SW_SHOWNOACTIVATE

    def update(self, opacity, offset):
        self.opacity=opacity
        self.user.SetLayeredWindowAttributes(self.hwnd,rgb(1,2,3),round(opacity*255),1|2)
        self.user.SetWindowPos(self.hwnd,W.HWND(-1),self.left,self.top+round(offset*self.scale),
                               self.width,self.height,0x10)

    def paint(self, hwnd):
        paint=PAINTSTRUCT();hdc=self.user.BeginPaint(hwnd,C.byref(paint))
        try:
            rectangle=W.RECT();self.user.GetClientRect(hwnd,C.byref(rectangle))
            if not self.fonts or not self.brush:return
            self.user.FillRect(hdc,C.byref(rectangle),self.brush)
            self.gdi.SetBkMode(hdc,1)
            accent=self.gdi.CreateSolidBrush(rgb(105,207,255))
            line=W.RECT(rectangle.right//2-round(35*self.scale),round(12*self.scale),
                        rectangle.right//2+round(35*self.scale),round(14*self.scale))
            self.user.FillRect(hdc,C.byref(line),accent);self.gdi.DeleteObject(accent)
            for text,font,y,color in ((self.header,self.fonts[0],27,rgb(185,233,255)),
                                      (self.text,self.fonts[1],61,rgb(237,247,255))):
                previous=self.gdi.SelectObject(hdc,font)
                try:
                    top=round(y*self.scale);bottom=top+round(30*self.scale)
                    shadow=W.RECT(1,top+1,rectangle.right+1,bottom+1)
                    self.gdi.SetTextColor(hdc,rgb(20,35,46))
                    self.user.DrawTextW(hdc,text,-1,C.byref(shadow),1|0x20|0x800)
                    foreground=W.RECT(0,top,rectangle.right,bottom)
                    self.gdi.SetTextColor(hdc,color)
                    self.user.DrawTextW(hdc,text,-1,C.byref(foreground),1|0x20|0x800)
                finally:self.gdi.SelectObject(hdc,previous)
        finally:self.user.EndPaint(hwnd,C.byref(paint))

    def window_proc(self, hwnd, message, wparam, lparam):
        if message==0xf:
            self.paint(hwnd);return 0
        if message==0x14:return 1
        if message==0x21:return 3  # MA_NOACTIVATE
        return self.user.DefWindowProcW(hwnd,message,wparam,lparam)

    def hide(self):
        if self.hwnd:self.user.ShowWindow(self.hwnd,0)

    def close(self):
        if self.hwnd:self.user.DestroyWindow(self.hwnd);self.hwnd=None
        for font in self.fonts:self.gdi.DeleteObject(font)
        self.fonts=[]
        if self.brush:self.gdi.DeleteObject(self.brush);self.brush=None
        if self.registered:self.user.UnregisterClassW(self.class_name,self.module);self.registered=False
