"""One local instance and an intentional hand pose for showing the existing window."""
import ctypes
from ctypes import wintypes as W
import hashlib
from pathlib import Path
from project_paths import PROJECT_ROOT


class WakeGesture:
    def __init__(self, hold=.6, release=.4):
        self.hold, self.release = hold, release
        self.key = self.since = self.last = self.released = None
        self.armed = True

    def suppress(self):
        self.armed = False
        self.key = self.since = self.released = None

    def update(self, hands, identity, now, enabled=True):
        poses = sorted(h['number'] for h in hands if h.get('gesture') == 'ILoveYou')
        continuous = self.last is not None and 0 <= now-self.last < .35
        self.last = now
        if not enabled:
            self.key = self.since = None
            return False
        if not poses:
            self.key = self.since = None
            if self.released is None or not continuous: self.released = now
            if now-self.released >= self.release: self.armed = True
            return False
        self.released = None
        key = (identity, poses[0]) if identity is not None else None
        if key is None or not self.armed:
            self.key = self.since = None
            return False
        if not continuous or key != self.key:
            self.key, self.since = key, now
        if now-self.since < self.hold: return False
        self.suppress()
        return True


class SingleInstance:
    """Session-scoped mutex + auto-reset activation event; no listening network port."""
    def __init__(self, project=PROJECT_ROOT):
        token = hashlib.sha256(str(Path(project).resolve()).casefold().encode()).hexdigest()[:24]
        self.name = 'Local\\VisionControl-'+token
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = (W.LPVOID, W.BOOL, W.LPCWSTR)
        self.kernel.CreateMutexW.restype = W.HANDLE
        self.kernel.CreateEventW.argtypes = (W.LPVOID, W.BOOL, W.BOOL, W.LPCWSTR)
        self.kernel.CreateEventW.restype = W.HANDLE
        self.kernel.SetEvent.argtypes = (W.HANDLE,)
        self.kernel.SetEvent.restype = W.BOOL
        self.kernel.WaitForSingleObject.argtypes = (W.HANDLE, W.DWORD)
        self.kernel.WaitForSingleObject.restype = W.DWORD
        self.kernel.CloseHandle.argtypes = (W.HANDLE,)
        self.kernel.CloseHandle.restype = W.BOOL
        ctypes.set_last_error(0)
        self.mutex = self.kernel.CreateMutexW(None, False, self.name+'-mutex')
        existed = ctypes.get_last_error() == 183
        if not self.mutex: raise ctypes.WinError(ctypes.get_last_error())
        self.primary = not existed
        self.event = self.kernel.CreateEventW(None, False, False, self.name+'-show')
        if not self.event:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def activate(self):
        if not self.kernel.SetEvent(self.event): raise ctypes.WinError(ctypes.get_last_error())

    def take_activation(self):
        return self.primary and self.kernel.WaitForSingleObject(self.event, 0) == 0

    def close(self):
        for name in ('event','mutex'):
            handle = getattr(self,name,None)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self,name,None)

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
