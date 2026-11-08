"""Bounded session event stream. No frames, embeddings or command input."""
from collections import deque
from datetime import datetime, timezone, timedelta
import threading


class ActivityFeed:
    def __init__(self, capacity=256):
        self.lock=threading.RLock()
        self.events=deque(maxlen=capacity)
        self.serial=0
        self.current=()
        self.last_identity=None
        self.missing_since=None
        self.camera_error=None

    def add(self, message):
        # Keep an event on one terminal line; user names have separate validation.
        message=' '.join(str(message).splitlines())
        with self.lock:
            self.serial+=1
            stamp=datetime.now(timezone(timedelta(hours=3))).strftime('%H:%M:%S')
            self.events.append((self.serial,f'{stamp}  {message}'))

    def read(self, cursor=None, tail=12):
        with self.lock:
            events=list(self.events)
            if cursor is None:events=events[-tail:]
            else:events=[event for event in events if event[0]>cursor]
            return [line for _,line in events],self.serial

    def observe(self, identities, profiles, now):
        known=tuple(sorted(identity for identity in set(identities)
                           if identity in profiles and profiles[identity].active))
        if len(identities)>1:
            target=('MULTIPLE',)
        elif len(identities)==1 and len(known)==1:
            target=known
        else:target=()
        if target==self.current:
            self.missing_since=None
            return
        if not target:
            if self.missing_since is None:self.missing_since=now
            if now-self.missing_since<.35:return
            if self.current and self.current!=('MULTIPLE',):
                profile=profiles.get(self.current[0])
                name=profile.name if profile else 'Recognized user'
                self.add(f'Face tracking lost · {name}')
        elif target==('MULTIPLE',):
            self.add('Multiple faces detected · management unavailable')
        else:
            profile=profiles[target[0]]
            verb='reconnected' if target[0]==self.last_identity else 'authenticated'
            self.add(f'{profile.name} {verb} · {profile.role}')
            self.last_identity=target[0]
        self.current=target
        self.missing_since=None
        self.camera_error=None

    def fail(self, error):
        if str(error)!=self.camera_error:
            self.add('Camera unavailable · '+str(error))
        self.camera_error=str(error)
        self.current=()
        self.missing_since=None
