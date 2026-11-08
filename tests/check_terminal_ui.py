"""Check window resizing and terminal integration without a camera."""
import _bootstrap  # Resolve application imports from any working directory.
from pathlib import Path
from tempfile import TemporaryDirectory
import ctypes
import time
import tkinter as tk
import numpy as np
from person_store import save_person
from profile_store import ProfileRepository
from admin_commands import PresenceTracker
from _ui_helpers import close_app
from recognition_ui import RecognitionWindow
from test_registration import vector
from vision_hud import demo_frame, draw_hud, BLUE


def main():
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError): pass
    with TemporaryDirectory() as tmp:
        directory, legacy = Path(tmp)/'people', Path(tmp)/'absent.npz'
        for index, (name, role, fields) in enumerate((
                ('Admin','ADMIN',('Software','Mechanics')), ('Deniz','USER',('Robotics',)),
                ('Ece','SUPERVISOR',('Electronics',)))):
            save_person(name, fields, role, [vector(index)]*3, directory, legacy)
        repo = ProfileRepository(directory,legacy)
        root = tk.Tk()
        app = RecognitionWindow(root, repo=repo, start_camera=False)
        now = [time.monotonic()]
        app.gate.clock = lambda: now[0]
        root.update()
        frame, face = demo_frame()
        admin = repo.find('Admin')
        profiles = {p.identity:p for p in repo.people()}
        display = draw_hud(frame,[(face,admin.identity,BLUE,.8)],[30],profiles)
        app.accept_frame(display,[admin.identity],profiles,now[0])
        assert app.canvas.itemcget(app.button_item,'state')=='normal'
        for mode in ('normal','maximized','fullscreen','restored'):
            if mode=='maximized': root.state('zoomed')
            elif mode=='fullscreen': root.attributes('-fullscreen',True)
            elif mode=='restored':
                root.attributes('-fullscreen',False); root.state('normal'); root.geometry('960x720')
            root.update(); time.sleep(.15); root.update(); app.redraw(); root.update()
            w,h=app.photo.width(),app.photo.height()
            assert w<=app.canvas.winfo_width() and h<=app.canvas.winfo_height()
            assert abs(w/h-4/3)<.01, (mode,w,h)
            bx,by=app.canvas.coords(app.button_item)
            assert 0<by<app.canvas.winfo_height()
        app.open_panel(); root.update()
        terminal=app.terminal
        assert terminal and not terminal.locked
        assert terminal.window.winfo_toplevel() is root
        assert not any(isinstance(child, tk.Toplevel) for child in root.winfo_children())
        for mode in ('small','maximized','fullscreen','restored'):
            if mode=='small': root.geometry('640x480')
            elif mode=='maximized': root.state('zoomed')
            elif mode=='fullscreen': root.attributes('-fullscreen',True)
            else:
                root.attributes('-fullscreen',False);root.state('normal');root.geometry('960x720')
            root.update();time.sleep(.1);root.update()
            assert terminal.entry.winfo_ismapped() and terminal.footer.winfo_ismapped()
            for widget in (terminal.entry,terminal.footer,terminal.back_button):
                x=widget.winfo_rootx()-terminal.window.winfo_rootx()
                y=widget.winfo_rooty()-terminal.window.winfo_rooty()
                assert 0<=x and 0<=y
                assert y+widget.winfo_height()<=terminal.window.winfo_height()
                assert x+widget.winfo_width()<=terminal.window.winfo_width()
        # Typing q in the terminal must leave the main window open.
        root.focus_force();terminal.entry.focus_set();root.update()
        terminal.entry.event_generate('<KeyPress>',keysym='q');root.update()
        assert not app.closed and root.winfo_exists()
        terminal.entry.delete(0,'end')
        assert 'LAST LOGIN' in terminal.output.get('1.0','end')
        terminal.entry.insert(0,'user D');terminal.complete()
        assert terminal.entry.get()=='user Deniz'
        terminal.entry.delete(0,'end'); terminal.entry.insert(0,'user Deniz info'); terminal.submit()
        assert 'FACE SAMPLES' in terminal.output.get('1.0','end')
        terminal.recall(-1); assert terminal.entry.get()=='user Deniz info'
        terminal.entry.delete(0,'end');terminal.entry.insert(0,'user edit Deniz field Design');terminal.submit()
        assert repo.find('Deniz').fields==('Design',)
        terminal.execute('user Deniz remove')
        terminal.entry.insert(0,'n');terminal.submit()
        assert repo.find('Deniz') is not None
        terminal.execute('help')
        help_text=terminal.output.get('1.0','end')
        for command in ('user base','user NAME info','user edit NAME name NEW_NAME',
                        'user edit NAME field FIELD','user edit NAME role ROLE',
                        'user NAME disable','user NAME enable','user NAME remove',
                        'status','whoami','logs','help','clear','lock','exit',
                        'sudo user edit NAME name NEW_NAME','sudo user edit NAME field FIELD',
                        'sudo user edit NAME role ROLE','sudo user NAME disable',
                        'sudo user NAME enable','sudo user NAME remove'):
            assert command in help_text,command
        # Keep processing frames while the panel is open.
        now[0]+=5
        next_frame=display.copy();next_frame[:20,:20]=(255,80,20)
        app.accept_frame(next_frame,[admin.identity],profiles,now[0])
        assert app.display is next_frame and app.gate.last_seen==now[0]
        assert app.terminal is terminal and app.gate.valid(admin.identity)
        terminal.clear();terminal.execute('user base');terminal.execute('user Admin info')
        root.update()
        # Limit the optional screenshot to this app's window.
        import os
        screenshot=os.environ.get('VISION_UI_SCREENSHOT')
        if screenshot:
            from PIL import ImageGrab
            time.sleep(.25); root.update()
            ctypes.windll.dwmapi.DwmFlush()
            hwnd=ctypes.windll.user32.GetAncestor(root.winfo_id(),2)
            ImageGrab.grab(window=hwnd).save(screenshot)
        now[0]+=4.99
        assert app.gate.valid(admin.identity)
        now[0]+=.01
        terminal.check_session()
        assert terminal.locked and not terminal.entry.get()
        assert 'LAST LOGIN' not in terminal.output.get('1.0','end')
        app.gate.observe(repo.find('Ece'),now[0]);app.open_panel();root.update()
        assert app.terminal.actor_identity==repo.find('Ece').identity and not app.terminal.locked
        app.terminal.manual_lock()
        app.terminal.back_button.invoke();root.update()
        assert app.terminal is None and app.canvas.winfo_ismapped()
        app.gate.observe(admin,now[0]);app.open_panel();root.update()
        app.escape();root.update()
        assert app.terminal is None and not app.closed
        app.gate.observe(admin,now[0]);app.open_panel();root.update()
        app.terminal.execute('exit');root.update()
        assert app.terminal is None and not app.closed
        app.gate.observe(repo.find('Deniz'),now[0]);app.update_button()
        assert app.canvas.itemcget(app.button_item,'state')=='hidden'
        close_app(app)
    print('Single-window UI passed: overlay, camera updates, resizing, help, q typing, Back/Esc/exit, commands and 5s lock.')


if __name__=='__main__': main()
