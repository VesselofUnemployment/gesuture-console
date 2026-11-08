"""On-demand screen capture and 1 Hz read-only hardware sampling.

Neither service runs camera inference or performs I/O on the Tk/hand loop.
"""
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime
from pathlib import Path
import os
import threading
import uuid
from windows_tray import signature


class MemoryStatus(C.Structure):
    _fields_=[('length',W.DWORD),('load',W.DWORD),
              *[(name,C.c_ulonglong) for name in ('total','available','page_total',
                'page_available','virtual_total','virtual_available','extended')]]


class Utilization(C.Structure):
    _fields_=[('gpu',C.c_uint),('memory',C.c_uint)]


def cpu_percent(previous, current):
    if previous is None:return None
    idle,kernel,user=(b-a for a,b in zip(previous,current))
    total=kernel+user
    return max(0.,min(100.,100*(total-idle)/total)) if total>0 else None


class HardwareSampler:
    def __init__(self):
        self.kernel=C.WinDLL('kernel32',use_last_error=True)
        signature(self.kernel,'GetSystemTimes',W.BOOL,C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME))
        signature(self.kernel,'GlobalMemoryStatusEx',W.BOOL,C.POINTER(MemoryStatus))
        self.previous=None;self.nvml=None;self.device=C.c_void_p()
        try:
            # Load the installed NVIDIA driver library, never a working-directory DLL.
            library=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/nvml.dll'
            self.nvml=C.CDLL(str(library))
            signature(self.nvml,'nvmlInit_v2',C.c_int)
            signature(self.nvml,'nvmlShutdown',C.c_int)
            signature(self.nvml,'nvmlDeviceGetHandleByIndex_v2',C.c_int,C.c_uint,C.POINTER(C.c_void_p))
            signature(self.nvml,'nvmlDeviceGetUtilizationRates',C.c_int,C.c_void_p,C.POINTER(Utilization))
            if self.nvml.nvmlInit_v2()!=0:self.nvml=None
            elif self.nvml.nvmlDeviceGetHandleByIndex_v2(0,C.byref(self.device))!=0:
                self.nvml.nvmlShutdown();self.nvml=None
        except (OSError,AttributeError):self.nvml=None

    def sample(self):
        times=[W.FILETIME() for _ in range(3)]
        cpu=None
        if self.kernel.GetSystemTimes(*(C.byref(t) for t in times)):
            current=tuple((t.dwHighDateTime<<32)|t.dwLowDateTime for t in times)
            cpu=cpu_percent(self.previous,current);self.previous=current
        memory=MemoryStatus();memory.length=C.sizeof(memory)
        ram=used=total=None
        if self.kernel.GlobalMemoryStatusEx(C.byref(memory)):
            ram=memory.load;total=memory.total/(1024**3)
            used=(memory.total-memory.available)/(1024**3)
        gpu=None
        if self.nvml:
            utilization=Utilization()
            if self.nvml.nvmlDeviceGetUtilizationRates(self.device,C.byref(utilization))==0:
                gpu=utilization.gpu
        return dict(cpu=cpu,ram=ram,used=used,total=total,gpu=gpu)

    def close(self):
        if self.nvml:self.nvml.nvmlShutdown();self.nvml=None


class ToolServices:
    def __init__(self, directory=None, grabber=None, sampler_factory=HardwareSampler):
        self.directory=Path(directory) if directory else Path(__file__).resolve().parent.parent/'captures'
        self.grabber=grabber;self.sampler_factory=sampler_factory
        self.lock=threading.Lock();self.wake=threading.Event();self.stop=threading.Event()
        self.hud=False;self.sample={};self.message=None;self.capture_epoch=0
        self.capture_thread=None;self.metrics_thread=None

    def set_hud(self, enabled):
        enabled=bool(enabled)
        if self.hud==enabled:return
        self.hud=enabled;self.sample={};self.wake.set()
        if enabled and self.metrics_thread is None:
            self.metrics_thread=threading.Thread(target=self._sample,name='vision-system-hud',daemon=True)
            self.metrics_thread.start()

    def _sample(self):
        sampler=None
        try:
            while not self.stop.is_set():
                if not self.hud:
                    if sampler:sampler.close();sampler=None
                    self.wake.wait();self.wake.clear();continue
                try:
                    if sampler is None:sampler=self.sampler_factory()
                    sample=sampler.sample()
                except (OSError,ValueError):sample={}
                if self.hud:self.sample=sample
                self.wake.wait(1.);self.wake.clear()
        finally:
            if sampler:sampler.close()

    def metrics(self):return self.sample

    def capture(self, rectangle):
        if rectangle is None or rectangle.width<1 or rectangle.height<1:return False
        if self.capture_thread and self.capture_thread.is_alive():return False
        with self.lock:epoch=self.capture_epoch
        self.capture_thread=threading.Thread(target=self._capture,args=(rectangle,epoch),
                                             name='vision-region-capture',daemon=True)
        self.capture_thread.start();return True

    def _capture(self, rectangle, epoch):
        temporary=None
        try:
            with self.lock:
                if epoch!=self.capture_epoch or self.stop.is_set():return
            if self.grabber:grab=self.grabber
            else:
                from PIL import ImageGrab
                grab=ImageGrab.grab
            bbox=(rectangle.left,rectangle.top,rectangle.right,rectangle.bottom)
            image=grab(bbox=bbox,all_screens=True,include_layered_windows=False)
            try:
                self.directory.mkdir(parents=True,exist_ok=True)
                name='capture-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.png'
                final=self.directory/name;temporary=self.directory/('.'+name+'.tmp')
                image.save(temporary,format='PNG')
            finally:image.close()
            with self.lock:
                if epoch!=self.capture_epoch or self.stop.is_set():return
                # Windows rename fails if the final name already exists.
                temporary.rename(final);temporary=None
                self.message='SAVED / '+final.name
        except (OSError,ValueError) as error:
            with self.lock:
                if epoch==self.capture_epoch:self.message='CAPTURE FAILED / '+str(error)[:65]
        finally:
            if temporary:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass

    def result(self):
        with self.lock:result=self.message;self.message=None
        return result

    def cancel_capture(self):
        with self.lock:self.capture_epoch+=1;self.message=None

    def deactivate(self):
        self.set_hud(False);self.cancel_capture()

    def close(self):
        self.deactivate();self.stop.set();self.wake.set()
        for thread in (self.capture_thread,self.metrics_thread):
            if thread and thread is not threading.current_thread():thread.join(timeout=.2)
