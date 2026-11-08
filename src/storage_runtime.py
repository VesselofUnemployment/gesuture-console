"""Cross-process storage serialization and recoverable file transactions.

The OS releases the lock after a crash. A durable journal plus a SQLite commit
marker determines whether file operations must be completed or rolled back.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import json
import os
import threading
import time
import uuid

_guard = threading.Lock()
_locks = {}


@contextmanager
def profile_lock(directory, timeout=5.):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with _guard:
        lock, local = _locks.setdefault(str(directory), (threading.RLock(), threading.local()))
    with lock:
        if getattr(local, 'depth', 0):
            local.depth += 1
            try:
                yield
            finally:
                local.depth -= 1
            return
        with (directory / '.profiles.lock').open('a+b') as handle:
            handle.seek(0, 2)
            if not handle.tell():
                handle.write(b'0')
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Profile storage is busy. Please try again.')
                    time.sleep(.01)
            local.depth = 1
            try:
                yield
            finally:
                local.depth = 0
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def serialized(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with profile_lock(self.db.parent):
            return method(self, *args, **kwargs)
    return run


class FileJournal:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / 'file-journal'

    def checked(self, value):
        path = Path(value)
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError('Invalid journal file path.')
        return path

    def prepare(self, kind, identity, moves=(), original=None, backup=None, pending=None):
        token = uuid.uuid4().hex
        record = dict(kind=kind, identity=identity,
                      moves=[[str(self.checked(a)), str(self.checked(b))] for a, b in moves])
        if original is not None:
            record.update(original=str(self.checked(original)), backup=str(self.checked(backup)),
                          pending=str(self.checked(pending)))
        self.directory.mkdir(exist_ok=True)
        path = self.directory / (token + '.json')
        temporary = path.with_suffix('.tmp')
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(record, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.rename(path)
        return token

    @staticmethod
    def mark(conn, token):
        conn.execute('INSERT INTO file_commits(id) VALUES(?)', (token,))

    def recover(self, conn):
        for path in sorted(self.directory.glob('*.json')):
            record = json.loads(path.read_text(encoding='utf-8'))
            committed = conn.execute('SELECT 1 FROM file_commits WHERE id=?', (path.stem,)).fetchone() is not None
            if record['kind'] == 'remove':
                for a, b in reversed(record['moves']):
                    source, pending = self.checked(a), self.checked(b)
                    if committed:
                        pending.unlink(missing_ok=True)
                    elif pending.exists():
                        if source.exists():
                            raise RuntimeError('Conflicting interrupted profile removal; originals preserved.')
                        pending.rename(source)
            elif record['kind'] == 'recapture':
                original = self.checked(record['original'])
                backup = self.checked(record['backup'])
                pending = self.checked(record['pending'])
                if not committed:
                    if not backup.is_file():
                        raise RuntimeError('Interrupted recapture backup missing; recovery required.')
                    with pending.open('wb') as output:
                        output.write(backup.read_bytes())
                        output.flush()
                        os.fsync(output.fileno())
                    pending.replace(original)
                pending.unlink(missing_ok=True)
            else:
                raise ValueError('Unknown file journal operation.')
            path.unlink()
            conn.execute('DELETE FROM file_commits WHERE id=?', (path.stem,))
        conn.commit()
