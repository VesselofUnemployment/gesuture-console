"""A short authority lease from an actual face match, separate from graphics."""
import time

LOSS_GRACE = 3.


class RecognitionPresence:
    def __init__(self, grace=LOSS_GRACE):
        self.grace = grace
        self.reset()

    def reset(self):
        self.person = None
        self.last_seen = float('-inf')

    def observe(self, person, timestamp, profiles, unsafe=False):
        if unsafe:
            self.reset()
        elif person is not None:
            self.person, self.last_seen = person, timestamp
        return self.current(timestamp, profiles)

    def current(self, now=None, profiles=None):
        now = time.monotonic() if now is None else now
        if self.person is None or not 0 <= now-self.last_seen < self.grace:
            self.reset()
            return None
        person = self.person if profiles is None else profiles.get(self.person.identity)
        if (person is None or not person.active or person.role != self.person.role or
                person.revision != self.person.revision):
            self.reset()
            return None
        return person
