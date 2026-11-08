"""Ask one verified Vision tray PID for an anonymous local summary. No camera."""
import _bootstrap
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import time
from project_paths import PROJECT_ROOT


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pid',type=int,required=True)
    args=parser.parse_args()
    user=C.WinDLL('user32',use_last_error=True)
    callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    user.EnumWindows.argtypes=(callback,W.LPARAM)
    user.GetWindowThreadProcessId.argtypes=(W.HWND,C.POINTER(W.DWORD))
    user.GetWindowTextW.argtypes=(W.HWND,W.LPWSTR,C.c_int)
    user.GetClassNameW.argtypes=(W.HWND,W.LPWSTR,C.c_int)
    user.PostMessageW.argtypes=(W.HWND,W.UINT,W.WPARAM,W.LPARAM)
    found=[]
    @callback
    def visit(hwnd,data):
        pid=W.DWORD();user.GetWindowThreadProcessId(hwnd,C.byref(pid))
        if pid.value==args.pid:
            title=C.create_unicode_buffer(256);name=C.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd,title,256);user.GetClassNameW(hwnd,name,256)
            if title.value == 'Vision Control Tray' and name.value.startswith('VisionControlTray-'+str(args.pid)+'-'):
                found.append(hwnd)
        return True
    user.EnumWindows(visit,0)
    if len(found)!=1:raise RuntimeError('Expected one verified Vision tray window.')
    started=time.time()
    if not user.PostMessageW(found[0],0x111,1006,0):raise C.WinError(C.get_last_error())
    path=PROJECT_ROOT/'.local'/'diagnostics'/'recognition-summary.json'
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        try:
            result=json.loads(path.read_text(encoding='utf-8'))
            if result['pid']==args.pid and result['created']>=started:
                print(json.dumps(result,indent=2));return
        except (OSError,ValueError,KeyError):pass
        time.sleep(.05)
    raise RuntimeError('Vision did not return a fresh diagnostic summary.')


if __name__=='__main__':main()
