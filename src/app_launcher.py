"""Load and launch local, explicitly configured application targets."""
import json
import os
from pathlib import Path
import queue
import re
import threading

from app_gestures import APP_NAMES, ACTION_NAMES
from project_paths import PROJECT_ROOT

GESTURE_CONFIG = PROJECT_ROOT / 'data' / 'gesture_shortcuts.json'
SESSION_CONFIG = GESTURE_CONFIG.with_name('session_shortcuts.json')
SESSION_KEYS = ('1', '2', '3', '4', '5')


def describe_bindings(names, session_count=0):
    labels = [f"{'Fist' if key == 'fist' else key} {names[key]}" for key in APP_NAMES]
    session = f'{session_count} configured' if session_count else 'Unassigned'
    return ' | '.join([*labels, f'SESSION START ({session})'])


DEFAULT_BINDINGS = describe_bindings(APP_NAMES)


def validate_targets(targets, *, keys=APP_NAMES):
    if not isinstance(targets, dict) or targets.keys() - set(keys):
        raise ValueError('Invalid application shortcuts.')
    for target in targets.values():
        if not isinstance(target, dict) or set(target) != {'kind', 'target'}:
            raise ValueError('Invalid shortcut.')
        value = target['target']
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ValueError('Invalid target.')
        if target['kind'] == 'app':
            if not re.fullmatch(r'[A-Za-z0-9_.!{}-]+', value):
                raise ValueError('Invalid Windows app identifier.')
        elif target['kind'] == 'file':
            path = Path(value)
            if not path.is_absolute() or path.suffix.lower() not in ('.exe', '.lnk'):
                raise ValueError('Shortcut must be an absolute EXE or LNK path.')
        else:
            raise ValueError('Unknown shortcut kind.')
    return targets


def load_targets(path, *, keys=APP_NAMES):
    try:
        text = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    return validate_targets(json.loads(text), keys=keys)


def target_name(target):
    value = target['target']
    return Path(value).stem if target['kind'] == 'file' else value.split('!', 1)[0]


def open_target(target):
    if target['kind'] == 'app':
        os.startfile('shell:AppsFolder\\' + target['target'])
    else:
        path = Path(target['target'])
        if not path.is_file():
            raise FileNotFoundError('Application shortcut not found: ' + str(path))
        os.startfile(str(path))


class AppLauncher:
    def __init__(self, targets=None, session_runner=None, *, session_targets=None):
        self.results = queue.SimpleQueue()
        self.busy = threading.Event()
        self.closed = threading.Event()
        self.thread = None
        self.session_runner = session_runner
        self.session_pending = {}
        self.error = self.session_error = ''
        try:
            self.targets = load_targets(GESTURE_CONFIG) if targets is None else targets
        except (OSError, ValueError) as error:
            self.targets, self.error = {}, str(error)
        try:
            self.session_targets = (load_targets(SESSION_CONFIG, keys=SESSION_KEYS)
                                    if session_targets is None else session_targets)
        except (OSError, ValueError) as error:
            self.session_targets, self.session_error = {}, str(error)
        self.names = ACTION_NAMES.copy()
        self.names.update({key: target_name(target) for key, target in self.targets.items()})

    @property
    def bindings(self):
        return describe_bindings(self.names, len(self.session_targets))

    def request(self, action):
        if self.closed.is_set() or self.busy.is_set() or action not in ACTION_NAMES:
            return False
        if action == 'session':
            if not self.session_targets and not self.session_error:
                return False
        elif action not in self.targets and not self.error:
            return False
        self.busy.set()
        self.thread = threading.Thread(target=self._open, args=(action,), daemon=True)
        self.thread.start()
        return True

    def _open(self, action):
        try:
            if self.closed.is_set():
                return
            if action == 'session':
                if self.session_error:
                    raise ValueError(self.session_error)
                from session_start import start_session
                ok, message = (self.session_runner or start_session)(
                    self.session_targets, self.closed, self.session_pending
                )
                self.results.put((action, ok, message))
            else:
                if self.error:
                    raise ValueError(self.error)
                open_target(self.targets[action])
                self.results.put((action, True, ''))
        except (OSError, ValueError) as error:
            self.results.put((action, False, str(error)))
        finally:
            self.busy.clear()

    def close(self):
        self.closed.set()
