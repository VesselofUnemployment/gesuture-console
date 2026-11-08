"""Launch the locally configured session apps without repeated requests."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
from pathlib import PureWindowsPath
import os
import time
from windows_tray import signature


@dataclass(frozen=True)
class Process:
    name:str
    path:str=''
    appid:str=''


class ProcessEntry(C.Structure):
    _fields_=[('dwSize',W.DWORD),('cntUsage',W.DWORD),('pid',W.DWORD),
              ('heap',C.c_size_t),('module',W.DWORD),('threads',W.DWORD),
              ('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),('name',W.WCHAR*260)]


def process_snapshot():
    """Read only relevant executable paths/AUMIDs in our Windows session.

    https://learn.microsoft.com/en-us/windows/win32/api/appmodel/nf-appmodel-getapplicationusermodelid
    """
    kernel=C.WinDLL('kernel32',use_last_error=True)
    signature(kernel,'CreateToolhelp32Snapshot',W.HANDLE,W.DWORD,W.DWORD)
    signature(kernel,'Process32FirstW',W.BOOL,W.HANDLE,C.POINTER(ProcessEntry))
    signature(kernel,'Process32NextW',W.BOOL,W.HANDLE,C.POINTER(ProcessEntry))
    signature(kernel,'OpenProcess',W.HANDLE,W.DWORD,W.BOOL,W.DWORD)
    signature(kernel,'CloseHandle',W.BOOL,W.HANDLE)
    signature(kernel,'QueryFullProcessImageNameW',W.BOOL,W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD))
    signature(kernel,'GetApplicationUserModelId',W.LONG,W.HANDLE,C.POINTER(W.UINT),W.LPWSTR)
    signature(kernel,'ProcessIdToSessionId',W.BOOL,W.DWORD,C.POINTER(W.DWORD))
    current=W.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(),C.byref(current)):raise C.WinError(C.get_last_error())
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot==C.c_void_p(-1).value:raise C.WinError(C.get_last_error())
    found=[]
    try:
        entry=ProcessEntry();entry.dwSize=C.sizeof(entry)
        more=kernel.Process32FirstW(snapshot,C.byref(entry))
        if not more:raise C.WinError(C.get_last_error())
        while more:
            name=entry.name.casefold();session=W.DWORD()
            if kernel.ProcessIdToSessionId(entry.pid, C.byref(session)) and session.value == current.value:
                path=appid='';handle=kernel.OpenProcess(0x1000,False,entry.pid)
                if handle:
                    try:
                        buffer=C.create_unicode_buffer(32768);size=W.DWORD(len(buffer))
                        if kernel.QueryFullProcessImageNameW(handle,0,buffer,C.byref(size)):path=buffer.value
                        app=C.create_unicode_buffer(512);length=W.UINT(len(app))
                        if kernel.GetApplicationUserModelId(handle,C.byref(length),app)==0:appid=app.value
                    finally:kernel.CloseHandle(handle)
                found.append(Process(name,path,appid))
            more=kernel.Process32NextW(snapshot,C.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    return found


def package_matches(path,appid):
    family=appid.split('!')[0]
    if '_' not in family:return False
    name,publisher=family.rsplit('_',1)
    return any(part.casefold().startswith(name.casefold()+'_') and
               part.casefold().endswith('__'+publisher.casefold()) for part in PureWindowsPath(path).parts)


def already_running(target, processes):
    """Return None when an exact executable name cannot be inspected."""
    value = target['target']
    if target['kind'] == 'app':
        appid = value.casefold()
        return any(process.appid.casefold() == appid or package_matches(process.path, value)
                   for process in processes)
    path = PureWindowsPath(value)
    if path.suffix.casefold() != '.exe':
        return False
    if any(process.path and PureWindowsPath(process.path) == path for process in processes):
        return True
    if any(process.name == path.name.casefold() and not process.path for process in processes):
        return None
    return False


def start_session(targets, stop, pending, snapshot=process_snapshot, opener=None, clock=time.monotonic):
    from app_launcher import SESSION_KEYS, open_target, target_name

    if stop.is_set():
        return False, 'SESSION START cancelled'
    selected = [targets[key] for key in SESSION_KEYS if key in targets]
    if not selected:
        return False, 'No Session Start apps configured.'
    opener = opener or open_target
    processes = snapshot()
    details = []
    requested = active = waiting = failed = 0
    seen = set()
    for target in selected:
        if stop.is_set():
            break
        label = target_name(target)
        value = target['target']
        key = (target['kind'], str(PureWindowsPath(value)).casefold()
               if target['kind'] == 'file' else value.casefold())
        if key in seen:
            continue
        seen.add(key)
        try:
            running = already_running(target, processes)
            if running is True:
                active += 1
                details.append(f'{label}: ACTIVE')
                continue
            if running is None:
                raise OSError('Could not inspect existing instance')
            if clock() - pending.get(key, float('-inf')) < 10:
                waiting += 1
                details.append(f'{label}: STARTING')
                continue
            if stop.is_set():
                break
            opener(target)
            pending[key] = clock()
            requested += 1
            details.append(f'{label}: OPEN REQUESTED')
        except (OSError, ValueError) as error:
            failed += 1
            details.append(f'{label}: {error}')
    heading = f'Opening {requested} / Active {active}'
    if waiting:
        heading += f' / Starting {waiting}'
    if failed:
        heading += f' / Failed {failed}'
    if stop.is_set():
        heading = 'SESSION START cancelled'
    return not failed and not stop.is_set(), heading + '\n' + '\n'.join(details)
