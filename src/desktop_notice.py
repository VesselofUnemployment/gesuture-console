"""Brief face-recognition text over the desktop; never opens or authorizes a panel.

Layered click-through/no-activation window behavior:
https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features
https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles
"""
import time
from native_notice_window import NativeNoticeWindow

HEADER = 'Face Recognised, Authority Active'


class NoticePresence:
    """Announce a stable identity once; brief face loss does not replay the welcome."""
    def __init__(self, stable=.25, absence=5.):
        self.stable, self.absence = stable, absence
        self.current = self.candidate = self.since = self.last = self.seen = None

    @staticmethod
    def key(person):
        return (person.identity, person.name, person.role, person.revision)

    def observe(self, person, now):
        continuous = self.last is not None and 0 <= now-self.last <= .75
        self.last = now
        if self.seen is not None and now-self.seen >= self.absence:
            self.current = None
        if person is None or not person.active:
            self.candidate = self.since = None
            return False
        key = self.key(person)
        self.seen = now
        if key == self.current:
            self.candidate = self.since = None
            return False
        if key != self.candidate or not continuous:
            self.candidate, self.since = key, now
        if now-self.since < self.stable:return False
        self.current = key
        self.candidate = self.since = None
        return True


def smooth(value):
    value=max(0.,min(1.,value))
    return value*value*(3-2*value)


def appearance(elapsed):
    """Opacity and small vertical entrance; finish after four seconds."""
    if elapsed < 0:return 0., 7., False
    if elapsed < .35:
        progress=smooth(elapsed/.35)
        return progress, 7*(1-progress), False
    if elapsed < 3.5:return 1., 0., False
    if elapsed < 4.:return 1-smooth((elapsed-3.5)/.5), 0., False
    return 0., 0., True


class DesktopNotice:
    def __init__(self, root):
        self.root=root
        self.window=None
        self.job=None
        self.closed=False
        self.policy=NoticePresence()
        self.key=self.last_valid=self.started=None
        self.require_presence=True

    def observe(self, person, timestamp, enabled=True):
        if self.closed:return
        now=time.monotonic()
        fresh=enabled and 0 <= now-timestamp <= .75
        valid=person if fresh and person is not None and person.active else None
        if self.policy.observe(valid,now):self.show(valid,now)
        if valid is not None:
            if self.key is not None and self.policy.key(valid)!=self.key:self.hide()
            self.last_valid=now
        elif not enabled:self.hide()

    def show(self, person, now=None):
        if self.closed:return
        self.hide()
        try:
            if self.window is None:self.window=NativeNoticeWindow(HEADER)
            self.window.header=HEADER
            self.require_presence=True
            self.key=self.policy.key(person)
            self.started=self.last_valid=time.monotonic() if now is None else now
            self.window.show(f'Welcome back, {person.name}')
            self.animate()
        except OSError:
            self.hide()  # A decoration cannot interrupt identity/hand inference.

    def show_message(self,header,text):
        if self.closed:return
        self.hide()
        try:
            if self.window is None:self.window=NativeNoticeWindow(HEADER)
            self.window.header=header
            self.require_presence=False
            self.started=time.monotonic()
            self.window.show(text)
            self.animate()
        except OSError:self.hide()

    def animate(self):
        self.job=None
        if self.closed or self.started is None:return
        now=time.monotonic()
        opacity,offset,finished=appearance(now-self.started)
        if finished or (self.require_presence and (self.last_valid is None or now-self.last_valid>=3.)):
            self.hide();return
        self.window.update(opacity,offset)
        self.job=self.root.after(25,self.animate)

    def hide(self):
        if self.job is not None:
            self.root.after_cancel(self.job);self.job=None
        self.started=self.key=None
        if self.window is not None:self.window.hide()

    def close(self):
        if self.closed:return
        self.hide();self.closed=True
        if self.window is not None:self.window.close()
