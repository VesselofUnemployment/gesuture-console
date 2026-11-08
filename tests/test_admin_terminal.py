"""Test commands, permission checks, and session locks with temporary profiles."""
import _bootstrap  # Resolve application imports from any working directory.
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import unittest
import numpy as np
from person_store import save_person, match_person, clean_name, clean_fields
from profile_store import ProfileRepository
from admin_commands import CommandEngine, SessionGate, PresenceTracker, MutationPlan
from test_registration import vector


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'people'
        self.legacy = Path(self.temp.name) / 'absent.npz'
        for index, (name, role) in enumerate([('Admin', 'ADMIN'), ('Deniz', 'USER'), ('Ece', 'SUPERVISOR'), ('Can', 'OPERATOR')]):
            save_person(name, ('Software',), role, [vector(index)] * 3, self.directory, self.legacy)
        self.repo = ProfileRepository(self.directory, self.legacy)
        self.admin, self.user, self.sup, self.op = [self.repo.find(name) for name in ('Admin', 'Deniz', 'Ece', 'Can')]
        self.allowed = True
        self.access = Path(self.temp.name) / 'codes.json'
        config = dict(version=1, algorithm='pbkdf2_sha256', iterations=100000, roles={})
        for index, role in enumerate(('OPERATOR','SUPERVISOR','ADMIN')):
            salt = bytes([index + 1]) * 16
            digest = hashlib.pbkdf2_hmac('sha256', ('test-' + role).encode(), salt, 100000)
            config['roles'][role] = dict(salt=salt.hex(), digest=digest.hex())
        self.access.write_text(json.dumps(config), encoding='utf-8')
        self.engine = self.make_engine(self.admin)

    def make_engine(self, actor):
        return CommandEngine(self.repo, actor.identity, lambda identity: self.allowed, self.access)

    def test_read_commands(self):
        base = self.engine.begin('user base').text
        self.assertIn('LAST LOGIN', base)
        self.assertIn('Admin', base)
        self.assertIn('NAME', base)
        self.assertIn('FACE SAMPLES', self.engine.begin('user deniz info').text)
        self.assertIn('ADMIN', self.engine.begin('whoami').text)
        self.assertIn('5 seconds', self.engine.begin('status').text)
        self.assertIn('REGISTER', self.engine.begin('logs').text)
        self.assertIn('user edit', self.engine.begin('help').text)
        for command in ('clear','lock','exit'):
            self.assertEqual(self.engine.begin(command).action, command)

    def test_names_and_syntax(self):
        for invalid in ('Ali Veli', 'a/b', '123', '"Ali"'):
            with self.assertRaises(ValueError): clean_name(invalid)
        for command in ('user edit Deniz name Ali Veli', 'user missing info', 'sudo', 'user Deniz shell', 'sudo user base'):
            with self.assertRaises(ValueError): self.engine.begin(command)
        for value in (['invalid'], ['Software','invalid']):
            with self.assertRaises(ValueError): clean_fields(value)
        self.engine.begin('user edit Deniz name Çağrı')
        self.assertIsNotNone(self.repo.find('ÇAĞRI'))
        with self.assertRaises(ValueError): self.engine.begin('user edit Çağrı name admin')

    def test_terminate_connection_exact_command_admin_and_active_session_only(self):
        for command in ('terminate connection','  TERMINATE   CONNECTION  '):
            self.assertEqual(self.engine.begin(command).action,'terminate')
        self.assertIn('terminate connection',self.engine.begin('help').text)
        for actor in (self.sup,self.user,self.op):
            with self.assertRaises(ValueError):self.make_engine(actor).begin('terminate connection')
        for command in ('terminate','connection terminate','terminate connection now','sudo terminate connection'):
            with self.assertRaises(ValueError):self.engine.begin(command)
        self.allowed=False
        with self.assertRaises(ValueError):self.engine.begin('terminate connection')

    def test_edit_persists_without_biometric_changes(self):
        hashes = {p: hashlib.sha256(p.read_bytes()).digest() for p in self.directory.glob('*.npz')}
        self.assertTrue(self.engine.begin('user edit Deniz field software+mechanics').changed)
        self.engine.begin('user edit Deniz role OPERATOR')
        self.engine.begin('user edit Deniz name Ada')
        other = ProfileRepository(self.directory, self.legacy)
        profile = other.find('Ada')
        self.assertEqual(profile.fields, ('Software','Mechanics'))
        self.assertEqual(profile.role, 'OPERATOR')
        self.assertTrue(profile.created_at)
        for path, digest in hashes.items(): self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)
        # Renaming frees the old name for a new registration.
        save_person('Deniz', ('Design',), 'USER', [vector(8)]*3, self.directory, self.legacy)
        self.assertEqual(len(other.people()), 5)

    def test_disabled_profiles_do_not_match(self):
        self.engine.begin('user Deniz disable')
        self.assertFalse(self.repo.find('Deniz').active)
        self.assertIsNone(match_person(vector(1), self.repo.people())[0])
        self.engine.begin('user Deniz enable')
        self.assertEqual(match_person(vector(1), self.repo.people())[0].identity, self.user.identity)

    def test_supervisor_requires_own_password_each_command(self):
        engine = self.make_engine(self.sup)
        with self.assertRaises(ValueError): engine.begin('user edit Deniz field Robotics')
        self.assertEqual(engine.begin('sudo user edit Deniz field Robotics').request, 'password')
        with self.assertRaises(ValueError): engine.reply('test-ADMIN')
        self.assertEqual(self.repo.find('Deniz').fields, ('Software',))
        engine.begin('sudo user edit Deniz field Robotics')
        self.assertTrue(engine.reply('test-SUPERVISOR').changed)
        self.assertEqual(self.repo.find('Deniz').fields, ('Robotics',))
        self.assertEqual(engine.begin('sudo user Can disable').request, 'password')
        self.assertTrue(engine.reply('test-SUPERVISOR').changed)
        engine.begin('sudo user Can enable'); engine.reply('test-SUPERVISOR')
        engine.begin('sudo user edit Can role USER'); engine.reply('test-SUPERVISOR')
        for command in ('sudo user Admin remove', 'sudo user Ece disable', 'sudo user edit Deniz role ADMIN'):
            with self.assertRaises(ValueError): engine.begin(command)
        logs = self.engine.begin('logs').text
        self.assertNotIn('test-', logs)
        self.assertIn('SUDO_DENIED', logs)

    def test_supervisor_remove_needs_password_then_confirmation(self):
        engine = self.make_engine(self.sup)
        engine.begin('sudo user Deniz remove')
        self.assertEqual(engine.reply('test-SUPERVISOR').request, 'confirm')
        engine.reply('n')
        self.assertIsNotNone(self.repo.find('Deniz'))
        engine.begin('sudo user Deniz remove'); engine.reply('test-SUPERVISOR')
        self.assertTrue(engine.reply('y').changed)
        self.assertIsNone(self.repo.find('Deniz'))

    def test_delete_cancel_confirm_and_revision_check(self):
        self.assertEqual(self.engine.begin('user Deniz remove').request, 'confirm')
        self.engine.reply('')
        self.assertIsNotNone(self.repo.find('Deniz'))
        self.engine.begin('user Deniz remove')
        self.make_engine(self.admin).begin('user edit Deniz name Ada')
        with self.assertRaises(ValueError): self.engine.reply('y')
        self.assertIsNotNone(self.repo.find('Ada'))
        self.engine.begin('user Ada remove'); self.engine.reply('y')
        self.assertIsNone(self.repo.find('Ada'))
        self.assertEqual(len(list(self.directory.glob('*.npz'))),3)
        self.assertEqual(list(self.directory.glob('*.removing')), [])

    def test_no_self_destruction_and_last_admin_protection(self):
        for command in ('user Admin remove','user Admin disable','user edit Admin role USER'):
            with self.assertRaises(ValueError): self.engine.begin(command)
        # Reject attempts to modify the last admin even when the test bypasses verification.
        plan = MutationPlan('disable',self.admin.identity,self.admin.name,self.admin.revision,sudo=True)
        with self.assertRaises(ValueError): self.repo.mutate(plan,self.sup.identity)
        self.engine.begin('user edit Admin name Boss')
        self.engine.begin('user edit Boss field Robotics')
        self.assertEqual(self.repo.get(self.admin.identity).name, 'Boss')

    def test_user_and_operator_have_no_terminal_access(self):
        for actor in (self.user, self.op):
            with self.assertRaises(ValueError): self.make_engine(actor).begin('user base')
        self.engine.begin('user Ece disable')
        with self.assertRaises(ValueError): self.make_engine(self.sup).begin('help')

    def test_session_rechecked_before_confirm_and_password(self):
        self.engine.begin('user Deniz remove')
        self.allowed = False
        with self.assertRaises(ValueError): self.engine.reply('y')
        self.assertIsNotNone(self.repo.find('Deniz'))
        self.allowed = True
        engine = self.make_engine(self.sup)
        engine.begin('sudo user edit Deniz name Ada')
        self.allowed = False
        with self.assertRaises(ValueError): engine.reply('test-SUPERVISOR')
        self.assertIsNotNone(self.repo.find('Deniz'))

    def test_exact_five_second_lock_and_fresh_candidate(self):
        now = [100.0]
        gate = SessionGate(clock=lambda: now[0])
        gate.observe(self.admin)
        self.assertEqual(gate.open(), self.admin.identity)
        now[0] = 104.99; gate.observe(None)
        self.assertTrue(gate.valid(self.admin.identity))
        now[0] = 105.0
        self.assertFalse(gate.valid(self.admin.identity))
        with self.assertRaises(ValueError): gate.open()
        gate.observe(self.admin); gate.open()
        gate.observe(self.sup)
        self.assertFalse(gate.valid(self.admin.identity))
        self.assertIsNone(gate.owner)
        gate.observe(self.admin); gate.open(); gate.observe(None, unsafe=True)
        self.assertFalse(gate.valid(self.admin.identity))
        gate.observe(self.user)
        with self.assertRaises(ValueError): gate.open()

    def test_last_login_is_presence_session_not_every_frame(self):
        tracker = PresenceTracker(self.repo)
        tracker.observe([self.user.identity], 0)
        tracker.observe([self.user.identity], .1)
        tracker.observe([self.user.identity], 9)
        tracker.observe([self.user.identity], 18.9)
        tracker.observe([self.user.identity], 29)
        logs = [r for r in self.repo.logs(100) if r['action']=='LOGIN']
        self.assertEqual(len(logs),2)
        self.assertTrue(self.repo.find('Deniz').last_login)
        self.assertEqual(self.repo.find('Deniz').revision,0)


if __name__ == '__main__': unittest.main()
