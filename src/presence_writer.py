"""Bounded, ordered login writes; the camera/Tk loop never waits for disk."""
import queue
import threading
import sqlite3
import time
import uuid
from admin_commands import PresenceTracker
from datetime import datetime, timezone
from runtime_lifecycle import LOG


class PresenceWriter:
    def __init__(self, repo):
        self.repo = repo
        self.queue = queue.Queue(maxsize=256)
        self.errors = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name='vision-login-writer', daemon=True)
        self.thread.start()

    def submit(self, name, *args):
        if self.stop.is_set():
            raise RuntimeError('Login writer is closing.')
        try:
            self.queue.put_nowait((name, args))
        except queue.Full:
            raise RuntimeError('Login storage queue is full.') from None

    def mark_login(self, identity):
        session_id = uuid.uuid4().hex
        self.submit('mark_login', identity, session_id, datetime.now(timezone.utc).isoformat(timespec='seconds'))
        return session_id

    def touch_login(self, *args):
        self.submit('touch_login', *args)

    def end_login(self, *args):
        self.submit('end_login', *args)

    def run(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                name, args = self.queue.get(timeout=.05)
            except queue.Empty:
                continue
            try:
                for attempt in range(3):
                    try:
                        getattr(self.repo, name)(*args)
                        break
                    except sqlite3.OperationalError as error:
                        if 'locked' not in str(error).lower() or attempt == 2:
                            raise
                        time.sleep(.02 * (attempt+1))
            except Exception as error:
                LOG.exception('Login storage failed')
                try:
                    self.errors.put_nowait(type(error).__name__ + ': ' + str(error))
                except queue.Full:
                    pass
            finally:
                self.queue.task_done()

    def check_error(self):
        try:
            error = self.errors.get_nowait()
        except queue.Empty:
            return
        raise RuntimeError('Login storage unavailable: ' + error)


class AsyncPresenceTracker(PresenceTracker):
    def __init__(self, repo):
        self.writer = PresenceWriter(repo)
        self.closing = False
        super().__init__(self.writer)

    def expire(self, now):
        self.writer.check_error()
        super().expire(now)

    def close(self):
        if not self.closing:
            try:
                super().close()
            finally:
                self.closing = True
                self.writer.stop.set()
        self.writer.thread.join(timeout=.01)
        return not self.writer.thread.is_alive()
