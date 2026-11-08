"""Local settings changes, separate from biometric profile storage."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from app_gestures import APP_NAMES
from app_launcher import GESTURE_CONFIG, validate_targets
from person_store import ACCESS_FILE, ROLES, find_access_role, read_access_config
from storage_runtime import profile_lock

PASSWORD_ROLES = ROLES[1:]


def write_json(path, config):
    content = (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    output = NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False)
    temporary = Path(output.name)
    try:
        with output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return content


def has_profiles(directory):
    if (directory / 'admin.npz').exists() or any((directory / 'people').glob('*.npz')):
        return True
    if any((directory / 'file-journal').glob('*.json')):
        return True
    database = directory / 'users.sqlite3'
    if not database.exists():
        return False
    connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return connection.execute('SELECT 1 FROM profiles LIMIT 1').fetchone() is not None
    finally:
        connection.close()


def require_admin(password, config):
    if not password or find_access_role(password, config) != 'ADMIN':
        raise ValueError('Incorrect current ADMIN password.')


def save_access_codes(current, changes, path=ACCESS_FILE):
    path = Path(path)
    with profile_lock(path.parent):
        try:
            config = read_access_config(path)
        except FileNotFoundError:
            if has_profiles(path.parent):
                raise ValueError('Profiles already exist. Restore your access settings from a backup to continue.')
            if set(changes) != set(PASSWORD_ROLES):
                raise ValueError('Set all three role passwords during initial setup.')
            config = dict(version=1, algorithm='pbkdf2_sha256', iterations=600_000, roles={})
        else:
            require_admin(current, config)

        if not changes or set(changes) - set(PASSWORD_ROLES):
            raise ValueError('Enter the role passwords you want to change.')
        if len(set(changes.values())) != len(changes):
            raise ValueError('Use a different password for each role.')
        roles = config['roles'].copy()
        for role, password in changes.items():
            if not 10 <= len(password) <= 128 or not password.strip():
                raise ValueError(f'The {role} password must be 10–128 characters long.')
            if config['roles'] and find_access_role(password, config) not in (None, role):
                raise ValueError(f'Choose a password for {role} that is different from the other roles.')
            salt = os.urandom(16)
            digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, config['iterations'])
            roles[role] = dict(salt=salt.hex(), digest=digest.hex())
        write_json(path, {**config, 'roles': roles})


def save_shortcuts(current, targets, path=GESTURE_CONFIG, access_path=ACCESS_FILE, previous=None, *, keys=APP_NAMES):
    path = Path(path)
    with profile_lock(Path(access_path).parent):
        try:
            config = read_access_config(access_path)
        except FileNotFoundError:
            raise ValueError('Complete setup in the Role Passwords tab first.') from None
        require_admin(current, config)
        if previous is not None:
            try:
                actual = path.read_bytes()
            except FileNotFoundError:
                actual = b''
            if actual != previous:
                raise ValueError('Another window updated the shortcuts. Close and reopen Settings.')
        validate_targets(targets, keys=keys)
        for target in targets.values():
            if target['kind'] == 'file' and not Path(target['target']).is_file():
                raise ValueError('File not found: ' + target['target'])
        return write_json(path, targets)
