import _bootstrap
import json
from pathlib import Path
import secrets
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app_gestures import APP_NAMES
from app_launcher import AppLauncher, SESSION_CONFIG, SESSION_KEYS, load_targets
from person_store import role_for_code
from settings_store import PASSWORD_ROLES, save_access_codes, save_shortcuts


class UserSettingsTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.access = self.directory / 'access_codes.json'
        self.shortcuts = self.directory / 'gesture_shortcuts.json'
        self.passwords = {role: secrets.token_hex(10) for role in PASSWORD_ROLES}

    def provision(self):
        save_access_codes('', self.passwords, self.access)
        return self.passwords['ADMIN']

    def test_first_setup_verifies_new_passwords_without_storing_them(self):
        with self.assertRaisesRegex(ValueError, 'three'):
            save_access_codes('', {'ADMIN': self.passwords['ADMIN']}, self.access)
        self.assertFalse(self.access.exists())
        self.provision()
        stored = self.access.read_text(encoding='utf-8')
        for role, password in self.passwords.items():
            self.assertNotIn(password, stored)
            self.assertEqual(role_for_code(password, self.access), role)
        self.assertEqual(role_for_code('', self.access), 'USER')

    def test_existing_profiles_prevent_reprovision_when_codes_are_missing(self):
        profile = self.directory / 'admin.npz'
        profile.write_bytes(b'temporary test profile')
        with self.assertRaisesRegex(ValueError, 'backup'):
            self.provision()
        self.assertFalse(self.access.exists())
        self.assertEqual(profile.read_bytes(), b'temporary test profile')

    def test_empty_profile_database_allows_first_setup(self):
        connection = sqlite3.connect(self.directory / 'users.sqlite3')
        connection.execute('CREATE TABLE profiles(identity TEXT)')
        connection.close()
        self.provision()
        self.assertTrue(self.access.is_file())

    def test_rotation_needs_admin_keeps_other_hashes_and_prevents_reuse(self):
        admin = self.provision()
        original = self.access.read_bytes()
        replacement = secrets.token_hex(10)
        for current, changes in (
            (self.passwords['SUPERVISOR'], {'ADMIN': replacement}),
            (admin, {'ADMIN': self.passwords['OPERATOR']}),
            (admin, {'ADMIN': replacement, 'SUPERVISOR': replacement}),
        ):
            with self.assertRaises(ValueError):
                save_access_codes(current, changes, self.access)
            self.assertEqual(self.access.read_bytes(), original)
        save_access_codes(admin, {'ADMIN': replacement}, self.access)
        updated = json.loads(self.access.read_bytes())
        for role in ('OPERATOR', 'SUPERVISOR'):
            self.assertEqual(updated['roles'][role], json.loads(original)['roles'][role])
        self.assertEqual(role_for_code(replacement, self.access), 'ADMIN')
        with self.assertRaises(ValueError):
            role_for_code(admin, self.access)

    def test_save_failure_preserves_existing_codes_and_cleans_staged_file(self):
        admin = self.provision()
        original = self.access.read_bytes()
        with patch('settings_store.Path.replace', side_effect=OSError('file locked')):
            with self.assertRaises(OSError):
                save_access_codes(admin, {'ADMIN': secrets.token_hex(10)}, self.access)
        self.assertEqual(self.access.read_bytes(), original)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_shortcut_validation_and_conflicting_edits(self):
        admin = self.provision()
        app = self.directory / 'Example Tool.exe'
        app.touch()
        shortcuts = {'1': dict(kind='file', target=str(app))}
        with self.assertRaises(ValueError):
            save_shortcuts(self.passwords['OPERATOR'], shortcuts, self.shortcuts, self.access)
        with self.assertRaises(ValueError):
            save_shortcuts(admin, {'2': dict(kind='app', target='Fake!App & cmd')}, self.shortcuts, self.access)
        self.assertFalse(self.shortcuts.exists())
        previous = save_shortcuts(admin, shortcuts, self.shortcuts, self.access, previous=b'')
        self.assertEqual(load_targets(self.shortcuts), shortcuts)
        with self.assertRaisesRegex(ValueError, 'Another window'):
            save_shortcuts(admin, {}, self.shortcuts, self.access, previous=b'')
        save_shortcuts(admin, {}, self.shortcuts, self.access, previous=previous)
        self.assertEqual(load_targets(self.shortcuts), {})

    def test_launcher_keeps_session_targets_separate_from_gestures(self):
        legacy = {key: dict(kind='app', target='Original!App') for key in SESSION_KEYS}
        current = {'1': dict(kind='app', target='NewApp!App')}
        seen = []

        def run_session(targets, stop, pending):
            seen.append(targets)
            return True, 'test'

        def load(path, **options):
            return legacy if path == SESSION_CONFIG else current

        with patch('app_launcher.load_targets', side_effect=load), patch('app_launcher.open_target') as opened:
            launcher = AppLauncher(session_runner=run_session)
            self.assertTrue(launcher.request('1'))
            launcher.thread.join(2)
            opened.assert_called_once_with(current['1'])
            self.assertTrue(launcher.request('session'))
            launcher.thread.join(2)
            self.assertEqual(seen, [legacy])
            self.assertIn('1 NewApp', launcher.bindings)
            self.assertIn('2 Unassigned', launcher.bindings)
            self.assertFalse(launcher.request('2'))
            opened.assert_called_once()
            launcher.close()
