"""Small failure boundaries shared by the Tk lifecycle and camera owners."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from background_runtime import SingleInstance
from project_paths import PROJECT_ROOT

LOG = logging.getLogger('vision')


def configure_logging():
    if LOG.handlers:
        return
    path = PROJECT_ROOT / '.local' / 'logs'
    path.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path / 'runtime.log', maxBytes=1_000_000, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.addHandler(handler)
    LOG.setLevel(logging.WARNING)


def cleanup_steps(steps):
    errors = []
    for name, action in steps:
        try:
            action()
        except Exception as error:
            errors.append((name, type(error).__name__))
            LOG.exception('Cleanup failed: %s', name)
    return errors


class CameraLease:
    """All project entry points agree on one owner per camera and data root."""
    def __init__(self, index, namespace=None):
        root = Path(namespace) if namespace is not None else PROJECT_ROOT / 'data'
        self.instance = SingleInstance(root / ('.camera-' + str(index)))
        if not self.instance.primary:
            self.instance.close()
            raise RuntimeError('Camera is already in use by Vision Control. Quit / release camera before registering a new person or running the demo.')

    def close(self):
        self.instance.close()


def process_resources():
    """Read only on an explicit diagnostics request, never on each frame."""
    import ctypes as C
    from ctypes import wintypes as W
    import os
    if os.name != 'nt':
        return {}
    class Counters(C.Structure):
        _fields_ = [('cb', W.DWORD), ('faults', W.DWORD)] + [
            (name, C.c_size_t) for name in ('peak_working', 'working', 'peak_paged', 'paged',
                                           'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]
    psapi = C.WinDLL('psapi', use_last_error=True)
    user = C.WinDLL('user32', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = (W.HANDLE, C.POINTER(Counters), W.DWORD)
    psapi.GetProcessMemoryInfo.restype = W.BOOL
    user.GetGuiResources.argtypes = (W.HANDLE, W.DWORD)
    user.GetGuiResources.restype = W.DWORD
    counters = Counters()
    counters.cb = C.sizeof(counters)
    process = W.HANDLE(-1)
    result = dict(gdi_handles=int(user.GetGuiResources(process, 0)),
                  user_handles=int(user.GetGuiResources(process, 1)))
    if psapi.GetProcessMemoryInfo(process, C.byref(counters), counters.cb):
        result.update(working_mb=round(counters.working/1024**2, 2),
                      private_mb=round(counters.private/1024**2, 2))
    return result
