"""Disposable own-window rendering/capture QA; no real shortcuts or camera."""
import _bootstrap
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import tempfile
import time
import tkinter as tk
import sys
from PIL import ImageGrab, Image
from desktop_windows import WindowBackend
from desktop_control import ControlPolicy, Rect
from desktop_tools import MENUS
from desktop_services import ToolServices, HardwareSampler
from control_overlay import ControlOverlay


def pump(root,seconds=.06):
    end=time.monotonic()+seconds
    while time.monotonic()<end:root.update();time.sleep(.005)


def main():
    try:C.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:pass
    failures=[];old_hook=sys.unraisablehook
    sys.unraisablehook=lambda error:failures.append(str(error.exc_value))
    backend=WindowBackend();area=backend.area
    root=tk.Tk();root.overrideredirect(True);root.configure(bg='#101e2c')
    root.geometry(f'{area.width}x{area.height}+{area.left}+{area.top}')
    root.attributes('-topmost',True)
    tk.Label(root,text='Vision Control / DISPOSABLE TOOL TEST',bg='#101e2c',fg='#aad5ed',
             font=('Segoe UI',16)).place(x=30,y=30)
    overlay=None;services=None
    try:
        pump(root,.2)
        overlay=ControlOverlay();policy=ControlPolicy(backend);policy.set_enabled(True)
        policy.point=backend.center;policy.tracking=True
        state=policy.view();state.update(mode='wheel',title='CONTROL',commands=None,hover=0)
        foreground=backend.user.GetForegroundWindow()
        overlay.show(state,backend);pump(root,.3);overlay.show(state,backend);pump(root)
        assert backend.user.GetForegroundWindow()==foreground
        out=Path(tempfile.gettempdir())/'control-tools-preview.png'
        box=overlay.wheel.last_bounds
        assert area.left<=box[0] and area.top<=box[1] and box[0]+box[2]<=area.right and box[1]+box[3]<=area.bottom
        ImageGrab.grab(bbox=(box[0],box[1],box[0]+box[2],box[1]+box[3]),
                       all_screens=True,include_layered_windows=True).save(out)
        for mode,commands in MENUS.items():
            state.update(mode=mode,commands=commands,title=mode.upper(),hover=0)
            overlay.show(state,backend);pump(root)
            assert overlay.wheel.active
        # Fixed metrics produce no repeated repaints while hand pointer moves.
        state.update(mode='draw',commands=None,hud=True,
                     metrics=dict(cpu=16.,ram=52.,gpu=7.,used=8.,total=16.),ink=[],ink_revision=0)
        overlay.show(state,backend);pump(root)
        hud_paints=overlay.hud.paint_count
        x,y=backend.center
        strokes=[[(x-90,y-70),(x+90,y-70)]]
        state.update(ink=strokes,ink_revision=1)
        overlay.show(state,backend);pump(root)
        bitmap=overlay.ink.bitmap
        start=time.perf_counter()
        for index in range(80):
            strokes[0].append((x+90+index*.8,y-70+index*.8))
            state['ink_revision']+=1;state['point']=(x+index,y)
            overlay.show(state,backend);root.update()
        elapsed=(time.perf_counter()-start)/80*1000
        assert overlay.ink.bitmap==bitmap,'Per-frame bitmap allocation'
        assert overlay.hud.paint_count==hud_paints,'Static HUD repaint'
        sample=ImageGrab.grab(bbox=(round(x-10),round(y-72),round(x+10),round(y-68)),
                              all_screens=True,include_layered_windows=True)
        assert any(b>150 and g>120 for r,g,b in sample.get_flattened_data()),'Ink not visible'
        state.update(ink=[],ink_revision=200)
        overlay.show(state,backend);pump(root);assert not overlay.ink.active
        state.update(ink=[[(x-20,y+50),(x+20,y+50)]],ink_revision=201)
        overlay.show(state,backend);pump(root)
        assert overlay.ink.active
        for surface in (overlay.wheel,overlay.pointer,overlay.outline,overlay.hud,overlay.ink):
            assert surface.get_style(surface.hwnd,-20)&(0x80000|0x20|0x8000000)==(0x80000|0x20|0x8000000)
        # Actual screen grab is restricted to plain pixels inside OUR fixture.
        overlay.hide();pump(root,.1)
        rectangle=Rect(area.left+80,area.top+100,area.left+200,area.top+180)
        with tempfile.TemporaryDirectory() as directory:
            services=ToolServices(directory)
            assert services.capture(rectangle)
            services.capture_thread.join(3.)
            assert 'SAVED' in services.result()
            files=list(Path(directory).glob('*.png'));assert len(files)==1
            with Image.open(files[0]) as image:
                assert image.size==(120,80)
                assert image.getpixel((60,40))==(16,30,44),image.getpixel((60,40))
            services.close();services=None
        sampler=HardwareSampler()
        try:
            sampler.sample();pump(root,1.05);metrics=sampler.sample()
            assert 0<=metrics['cpu']<=100 and 0<=metrics['ram']<=100,metrics
            assert metrics['total']>0 and metrics['used']>=0
            assert metrics['gpu'] is None or 0<=metrics['gpu']<=100,metrics
        finally:sampler.close()
        assert not failures,failures
        print(f'Tools UI passed: 8/4/8/5 slices, no focus/click interception, reusable incremental ink, HUD cache, real own-fixture capture, CPU/RAM/NVIDIA readings. Drawing show+paint mean {elapsed:.2f} ms (HUD rendering only). Preview: {out}')
    finally:
        if services:services.close()
        if overlay:overlay.close()
        root.destroy();sys.unraisablehook=old_hook


if __name__=='__main__':main()
