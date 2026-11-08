import _bootstrap
import ctypes as C
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from desktop_control import Rect
from desktop_services import ToolServices, cpu_percent
from desktop_windows import WindowBackend, INPUT


class InputTests(unittest.TestCase):
    def backend(self,send,held=()):
        backend=WindowBackend.__new__(WindowBackend)
        backend.mouse_down=False;backend.pending_keys=[]
        backend.user=SimpleNamespace(SendInput=send,GetAsyncKeyState=lambda key:0x8000 if key in held else 0)
        return backend

    def test_commands_are_balanced_correct_size_and_fixed_allowlist(self):
        batches=[]
        def send(count,events,size):
            self.assertEqual(size,40 if C.sizeof(C.c_void_p)==8 else 28)
            batches.append([(event.type,event.ki.wVk,event.ki.dwFlags) for event in events[:count]])
            return count
        backend=self.backend(send)
        for action in ('desk-left','desk-right','media-play','media-next','media-prev','volume-up','volume-down','volume-mute'):
            self.assertTrue(backend.command(action))
            batch=batches[-1];pressed=[]
            for kind,key,flags in batch:
                self.assertEqual(kind,1)
                if flags&2:pressed.remove(key)
                else:pressed.append(key)
            self.assertEqual(pressed,[])
        self.assertEqual([key for _,key,flags in batches[0] if not flags&2],[0x5b,0x11,0x25])
        self.assertEqual(batches[2][0][1],0xb3)
        self.assertFalse(backend.command('shell'));self.assertEqual(len(batches),8)

    def test_physical_modifier_prevents_injection_and_partial_batch_releases_only_ours(self):
        calls=[]
        backend=self.backend(lambda *args:calls.append(args),held=(0x12,))
        self.assertFalse(backend.command('desk-right'));self.assertEqual(calls,[])
        def send(count,events,size):
            calls.append([(events[i].ki.wVk,events[i].ki.dwFlags) for i in range(count)])
            if len(calls)==1:return 2  # Win and Ctrl went down, no Right key.
            return 0 if len(calls)==2 else count
        backend=self.backend(send)
        self.assertFalse(backend.command('desk-right'))
        self.assertEqual(backend.pending_keys,[0x5b,0x11])
        self.assertTrue(backend.release_mouse())
        self.assertEqual(backend.pending_keys,[])
        self.assertEqual([key for key,flags in calls[-1]],[0x11,0x5b])
        self.assertTrue(all(flags&2 for key,flags in calls[-1]))


class ServiceTests(unittest.TestCase):
    def test_cpu_kernel_includes_idle_and_zero_delta_is_unavailable(self):
        self.assertEqual(cpu_percent((100,300,200),(120,360,240)),80.)
        self.assertIsNone(cpu_percent(None,(1,2,3)))
        self.assertIsNone(cpu_percent((1,2,3),(1,2,3)))

    def test_capture_only_requested_region_png_unique_and_error_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            def grab(**kwargs):
                calls.append(kwargs);return Image.new('RGB',(80,60),(21,63,112))
            services=ToolServices(directory,grabber=grab)
            try:
                self.assertEqual(list(Path(directory).iterdir()),[])
                for _ in range(2):
                    self.assertTrue(services.capture(Rect(10,20,90,80)))
                    services.capture_thread.join(2.)
                    self.assertIn('SAVED',services.result())
                paths=list(Path(directory).glob('*.png'));self.assertEqual(len(paths),2)
                with Image.open(paths[0]) as image:
                    self.assertEqual(image.size,(80,60));self.assertEqual(image.getpixel((40,30)),(21,63,112))
                self.assertEqual(calls[0],dict(bbox=(10,20,90,80),all_screens=True,include_layered_windows=False))
                services.grabber=lambda **kwargs:(_ for _ in ()).throw(OSError('test unavailable'))
                services.capture(Rect(10,20,90,80));services.capture_thread.join(2.)
                self.assertIn('CAPTURE FAILED',services.result())
                self.assertEqual(len(list(Path(directory).glob('*.png'))),2)
            finally:services.close()

    def test_capture_cancel_during_grab_discards_file_and_worker_does_not_block_ui(self):
        ready=threading.Event();finish=threading.Event()
        def grab(**kwargs):
            ready.set();finish.wait(2.);return Image.new('RGB',(80,60))
        with tempfile.TemporaryDirectory() as directory:
            services=ToolServices(directory,grabber=grab)
            try:
                start=time.perf_counter();services.capture(Rect(10,20,90,80))
                self.assertLess(time.perf_counter()-start,.1)
                self.assertTrue(ready.wait(1.));self.assertFalse(services.capture(Rect(10,20,90,80)))
                services.deactivate();finish.set();services.capture_thread.join(2.)
                self.assertEqual(list(Path(directory).iterdir()),[])
                self.assertIsNone(services.result())
            finally:finish.set();services.close()

    def test_metrics_are_on_demand_one_hz_and_stopped_without_blocking_hand_loop(self):
        entered=threading.Event();allow=threading.Event();closed=threading.Event();calls=[]
        class Sampler:
            def sample(self):
                calls.append(time.monotonic());entered.set();allow.wait(1.)
                return {'cpu':12,'ram':50,'gpu':6}
            def close(self):closed.set()
        services=ToolServices(sampler_factory=Sampler)
        try:
            self.assertIsNone(services.metrics_thread)
            start=time.perf_counter();services.set_hud(True)
            self.assertLess(time.perf_counter()-start,.1);self.assertTrue(entered.wait(1.))
            self.assertEqual(services.metrics(),{})
            services.set_hud(False);allow.set()
            self.assertTrue(closed.wait(1.));self.assertEqual(services.metrics(),{})
            self.assertEqual(len(calls),1)
        finally:allow.set();services.close()


if __name__=='__main__':unittest.main()
