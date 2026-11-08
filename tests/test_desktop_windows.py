import _bootstrap
import ctypes as C
import unittest
from types import SimpleNamespace
from desktop_control import Rect
from desktop_windows import MINMAXINFO, WindowBackend, INPUT


class WindowLimitsTests(unittest.TestCase):
    def setUp(self):
        self.backend=WindowBackend.__new__(WindowBackend)
        self.backend.area=Rect(0,0,1920,1080);self.backend.scale=1.
        self.backend.valid=lambda target:True
        self.target=SimpleNamespace(hwnd=9,rect=Rect(100,100,1100,700))

    def test_failed_or_timed_out_query_never_guesses_a_smaller_minimum(self):
        self.backend.user=SimpleNamespace(GetSystemMetrics=lambda metric:200,
            SendMessageTimeoutW=lambda *args:0)
        self.assertEqual(self.backend.resize_limits(self.target),((1000,600),(1920,1080)))

    def test_app_constraints_override_defaults_with_bounded_timeout(self):
        calls=[]
        def send(hwnd,message,wparam,address,flags,timeout,result):
            calls.append((hwnd,message,flags,timeout))
            info=C.cast(address,C.POINTER(MINMAXINFO)).contents
            info.ptMinTrackSize.x=650;info.ptMinTrackSize.y=480
            info.ptMaxTrackSize.x=1600;info.ptMaxTrackSize.y=1300
            return 1
        self.backend.user=SimpleNamespace(GetSystemMetrics=lambda metric:200,SendMessageTimeoutW=send)
        self.assertEqual(self.backend.resize_limits(self.target),((650,480),(1600,1080)))
        self.assertEqual(calls,[(9,0x24,0x23,30)])


class MouseInputTests(unittest.TestCase):
    def setUp(self):
        self.backend=WindowBackend.__new__(WindowBackend);self.backend.mouse_down=False
        self.sent=[];self.positions=[];self.held=False;self.answers=[]
        def send(count,inputs,size):
            array=C.cast(inputs,C.POINTER(INPUT))
            self.sent.append((size,[(array[i].type,array[i].mi.dx,array[i].mi.dy,array[i].mi.dwFlags)
                                   for i in range(count)]))
            return self.answers.pop(0) if self.answers else count
        def position(x,y):self.positions.append((x,y));return 1
        metrics={76:-1920,77:0,78:3840,79:1080}
        self.backend.user=SimpleNamespace(SendInput=send,SetCursorPos=position,
            GetSystemMetrics=lambda index:metrics[index],GetAsyncKeyState=lambda key:0x8000 if self.held else 0)

    def test_positioned_click_is_one_batch_and_native_input_size_is_correct(self):
        self.assertEqual(C.sizeof(INPUT),40 if C.sizeof(C.c_void_p)==8 else 28)
        self.assertTrue(self.backend.click_left((0,540)))
        size,events=self.sent[0]
        self.assertEqual(size,C.sizeof(INPUT));self.assertEqual(len(events),3)
        self.assertEqual([e[3] for e in events],[0xc001,2,4])
        self.assertAlmostEqual(events[0][1],32776,delta=1)
        self.assertAlmostEqual(events[0][2],32798,delta=1)
        self.assertFalse(self.backend.mouse_down)

    def test_partial_click_releases_and_poll_can_retry_a_failed_release(self):
        self.answers=[2,0,1]
        self.assertFalse(self.backend.click_left((0,540)))
        self.assertTrue(self.backend.mouse_down)
        self.assertTrue(self.backend.release_mouse())
        self.assertFalse(self.backend.mouse_down)
        self.assertEqual([len(events) for _,events in self.sent],[3,1,1])
        self.assertEqual(self.sent[-1][1][0][3],4)

    def test_blocked_click_does_not_inject_a_spurious_release(self):
        self.answers=[0]
        self.assertFalse(self.backend.click_left((0,540)))
        self.assertFalse(self.backend.mouse_down);self.assertEqual(len(self.sent),1)

    def test_real_held_button_is_not_clicked_or_released_by_hand_control(self):
        self.held=True
        self.assertTrue(self.backend.click_left((0,540)))
        self.assertEqual(self.sent,[])
        self.assertTrue(self.backend.release_mouse());self.assertEqual(self.sent,[])

    def test_move_rounds_to_the_same_pixel_used_by_visible_pointer(self):
        self.assertTrue(self.backend.move_cursor((123.4,456.7)))
        self.assertEqual(self.positions,[(123,457)])
        self.assertEqual(self.sent,[])


if __name__=='__main__':unittest.main()
