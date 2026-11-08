"""Profile metadata and activity history."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import importlib
import io
import os
import uuid
import threading
import zipfile
from storage_runtime import serialized, FileJournal

import numpy as np
import re
import sqlite3

from person_store import BASE, PEOPLE, Person, clean_name, clean_fields, name_key, ROLES, load_raw_people, read_person


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def display_time(value, date_only=False):
    if not value:
        return "--"
    try:
        dt = datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=3)))
        return dt.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return "--"


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class ProfileRepository:
    def __init__(self, directory=PEOPLE, legacy=None):
        self.directory = Path(directory)
        self.legacy = Path(legacy) if legacy is not None else BASE / "data" / "admin.npz"
        self.db = self.directory.parent / "users.sqlite3"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self._cache_lock = threading.RLock()
        self._raw_cache = {}
        self.record_errors = {}
        self.journal = FileJournal(self.db.parent)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS file_commits (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS profiles (
                    identity TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
                    fields TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT '', last_login TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS login_sessions (
                    id TEXT PRIMARY KEY, identity TEXT NOT NULL, started_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL, ended_at TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'live'
                );
                CREATE INDEX IF NOT EXISTS login_identity ON login_sessions(identity);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL
                );
            """)
        self.sync()
        self.import_login_history()

    @classmethod
    def inspect_people(cls, directory=PEOPLE, legacy=None):
        """Read-only setup checks never create/migrate the user's database."""
        directory = Path(directory)
        raw = load_raw_people(directory, legacy)
        db = directory.parent / 'users.sqlite3'
        if not db.is_file():
            return raw
        conn = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = {row['identity']: row for row in conn.execute('SELECT * FROM profiles')}
        finally:
            conn.close()
        return [cls._with_metadata(person, rows[person.identity]) if person.identity in rows else person
                for person in raw]

    def connect(self):
        conn = sqlite3.connect(self.db, timeout=.2, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def _cached_person(self, path, legacy=False):
        path = Path(path)
        with self._cache_lock:
            try:
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
                cached = self._raw_cache.get(path)
                if cached is not None and cached[0] == signature:
                    if isinstance(cached[1], Exception):
                        return None
                    return cached[1]
                person = read_person(path, legacy)
                self._raw_cache[path] = (signature, person)
                self.record_errors.pop(path.name, None)
                return person
            except (OSError, ValueError, RuntimeError, KeyError, EOFError, zipfile.BadZipFile) as error:
                self.record_errors[path.name] = type(error).__name__ + ': ' + str(error)
                if 'signature' in locals():
                    self._raw_cache[path] = (signature, error)
                return None

    @serialized
    def sync(self):
        # Recovery precedes discovery, so an interrupted rename is never mistaken
        # for a deleted profile. Missing/corrupt sources never delete metadata.
        with self.connect() as conn:
            self.journal.recover(conn)
        paths = ([self.legacy] if self.legacy.is_file() else []) + sorted(self.directory.glob('*.npz'))
        raw = [person for path in paths
               if (person := self._cached_person(path, path == self.legacy)) is not None]
        with self._cache_lock:
            self._raw_cache = {path: item for path, item in self._raw_cache.items() if path in paths}
            self.record_errors = {name: error for name, error in self.record_errors.items()
                                  if name in {path.name for path in paths}}
        valid = []
        with self.connect() as conn:
            known = {row[0] for row in conn.execute('SELECT identity FROM profiles')}
            for person in raw:
                if person.identity not in known:
                    try:
                        conn.execute("INSERT INTO profiles(identity,name,name_key,fields,role,created_at) VALUES(?,?,?,?,?,?)",
                                     (person.identity, person.name, name_key(person.name), json.dumps(person.fields),
                                      person.role, person.created_at))
                        self.audit(conn, 'SYSTEM', 'REGISTER' if person.created_at else 'IMPORT', person.name, person.role)
                    except sqlite3.IntegrityError:
                        self.record_errors[self._source(person.identity).name] = 'Duplicate profile name; record preserved and excluded.'
                        continue
                valid.append(person)
            available = {person.identity for person in valid}
            for identity in known - available:
                self.record_errors.setdefault(self._source(identity).name,
                                              'Source unavailable; metadata preserved and access disabled.')
        return valid

    @staticmethod
    def _with_metadata(person, row):
        if row['role'] not in ROLES:
            raise ValueError('Profile role is invalid.')
        return replace(person, name=clean_name(row['name']), fields=clean_fields(json.loads(row['fields'])),
                       role=row['role'], active=bool(row['active']), created_at=row['created_at'],
                       last_login=row['last_login'], revision=row['revision'])

    def people(self):
        raw = self.sync()
        with self.connect() as conn:
            rows = {row['identity']: row for row in conn.execute('SELECT * FROM profiles')}
        result = []
        for person in raw:
            if person.identity not in rows:
                continue
            try:
                result.append(self._with_metadata(person, rows[person.identity]))
            except (ValueError, TypeError):
                self.record_errors[self._source(person.identity).name] = 'Invalid profile metadata; access disabled.'
        return result

    def get(self, identity):
        # Always read current metadata: a role/active/revision change must not
        # wait for an embedding cache TTL. This path never takes a write lock.
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM profiles WHERE identity=?', (identity,)).fetchone()
        if row is None:
            return None
        person = self._cached_person(self._source(identity), identity == 'Admin')
        if person is None:
            return None
        try:
            return self._with_metadata(person, row)
        except (ValueError, TypeError):
            self.record_errors[self._source(identity).name] = 'Invalid profile metadata; access disabled.'
            return None

    def find(self, name):
        with self.connect() as conn:
            row = conn.execute('SELECT identity FROM profiles WHERE name_key=?', (name_key(name),)).fetchone()
        if row is None:
            self.sync()
            with self.connect() as conn:
                row = conn.execute('SELECT identity FROM profiles WHERE name_key=?', (name_key(name),)).fetchone()
        return self.get(row['identity']) if row else None

    @staticmethod
    def audit(conn, actor, action, target="", detail=""):
        conn.execute("INSERT INTO audit(timestamp,actor,action,target,detail) VALUES(?,?,?,?,?)",
                     (utc_now(), actor, action, target, detail))

    def log(self, actor, action, target="", detail=""):
        with self.connect() as conn:
            self.audit(conn, actor, action, target, detail)

    def logs(self, limit=40):
        with self.connect() as conn:
            return list(reversed(conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()))

    def import_login_history(self):
        # Import legacy LOGIN entries only when the name matches a single profile.
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM metadata WHERE key='login_history_v1'").fetchone():
                return
            profiles = conn.execute("SELECT identity,name,created_at FROM profiles").fetchall()
            for event in conn.execute("SELECT * FROM audit WHERE action='LOGIN'").fetchall():
                try: key = name_key(event['target'] or event['actor'])
                except ValueError: continue
                matches = [p for p in profiles if name_key(p['name']) == key and
                           (not p['created_at'] or event['timestamp'] >= p['created_at'])]
                if len(matches) == 1:
                    conn.execute("INSERT OR IGNORE INTO login_sessions VALUES(?,?,?,?,?,?)",
                                 ('legacy:'+str(event['id']), matches[0]['identity'], event['timestamp'],
                                  event['timestamp'], event['timestamp'], 'legacy'))
            conn.execute("INSERT INTO metadata VALUES('login_history_v1','done')")

    def mark_login(self, identity, session_id=None, timestamp=None):
        with self.connect() as conn:
            row = conn.execute("SELECT name,active FROM profiles WHERE identity=?", (identity,)).fetchone()
            if row is not None and row["active"]:
                now, session_id = timestamp or utc_now(), session_id or uuid.uuid4().hex
                conn.execute("UPDATE profiles SET last_login=? WHERE identity=?", (now, identity))
                conn.execute("INSERT INTO login_sessions(id,identity,started_at,last_seen_at) VALUES(?,?,?,?)",
                             (session_id, identity, now, now))
                self.audit(conn, row["name"], "LOGIN", row["name"])
                return session_id
        return None

    def touch_login(self, session_id, timestamp=None):
        with self.connect() as conn:
            conn.execute("UPDATE login_sessions SET last_seen_at=? WHERE id=? AND ended_at=''",
                         (timestamp or utc_now(),session_id))

    def end_login(self, session_id, last_seen=None):
        with self.connect() as conn:
            conn.execute("UPDATE login_sessions SET ended_at=COALESCE(?,last_seen_at),last_seen_at=COALESCE(?,last_seen_at) WHERE id=? AND ended_at=''",
                         (last_seen,last_seen,session_id))

    def stats(self, identity, now=None):
        now = now or datetime.now(timezone.utc)
        local = timezone(timedelta(hours=3))
        today = now.astimezone(local).date()
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM login_sessions WHERE identity=? ORDER BY started_at",(identity,)).fetchall()
        today_rows = []
        for row in rows:
            try:
                if datetime.fromisoformat(row['started_at']).astimezone(local).date() == today:
                    today_rows.append(row)
            except ValueError: pass
        return dict(total=len(rows),today=len(today_rows),
                    first_today=min((r['started_at'] for r in today_rows),default=''),
                    first_recorded=min((r['started_at'] for r in rows),default=''),
                    legacy=sum(r['source']=='legacy' for r in rows))

    @staticmethod
    def check_recapture_rows(conn, plan, actor_identity):
        actor = conn.execute("SELECT * FROM profiles WHERE identity=?",(actor_identity,)).fetchone()
        target = conn.execute("SELECT * FROM profiles WHERE identity=?",(plan.target_identity,)).fetchone()
        if not actor or not actor['active'] or actor['role'] not in ('ADMIN','SUPERVISOR'):
            raise ValueError('Management access denied.')
        if plan.actor_role and actor['role'] != plan.actor_role:
            raise ValueError('Your access level changed. Run recapture again.')
        if not target or target['revision'] != plan.revision:
            raise ValueError('Profile changed. Run recapture again.')
        if actor['role']=='SUPERVISOR' and (not plan.sudo or target['role'] not in ('USER','OPERATOR')):
            raise ValueError('SUPERVISOR may recapture only USER / OPERATOR with sudo.')
        return actor,target

    def validate_recapture(self, plan, actor_identity):
        if plan.kind != 'recapture': raise ValueError('Invalid recapture request.')
        self.sync()
        with self.connect() as conn:
            self.check_recapture_rows(conn,plan,actor_identity)
        return self.get(plan.target_identity)

    @serialized
    def replace_templates(self, plan, actor_identity, samples):
        if plan.kind != 'recapture': raise ValueError('Invalid recapture request.')
        enrollment = importlib.import_module('face_enrollment')
        templates = enrollment.validate_templates(np.asarray(samples))
        original = self._source(plan.target_identity)
        pending = original.with_name(original.stem+'.'+uuid.uuid4().hex+'.pending')
        backup = None
        swapped = False
        self.sync()
        conn = self.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            actor,target = self.check_recapture_rows(conn,plan,actor_identity)
            before = original.read_bytes()
            with np.load(io.BytesIO(before),allow_pickle=False) as record:
                content = {key:record[key].copy() for key in record.files}
            old = enrollment.validate_templates(content['templates'])
            enrollment.validate_templates(templates,expected_count=len(old))
            # Require every replacement sample to match the original identity.
            if np.any(np.max(templates@old.T,axis=1)<0.65):
                raise ValueError('New samples do not match the existing profile.')
            for person in load_raw_people(self.directory,self.legacy):
                if person.identity != plan.target_identity:
                    own=np.max(templates@old.T,axis=1)
                    other=np.max(templates@person.templates.T,axis=1)
                    if np.any(other>=0.65) or np.any(own-other<0.05):
                        raise ValueError('Samples match another profile or are ambiguous.')
            content['templates']=templates
            with pending.open('xb') as output:
                np.savez_compressed(output,**content)
                output.flush();os.fsync(output.fileno())
            backup_dir=self._backup_dir(plan.target_identity)
            backup_dir.mkdir(parents=True,exist_ok=True)
            backup=backup_dir/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex+'.npz')
            with backup.open('xb') as output:
                output.write(before);output.flush();os.fsync(output.fileno())
            token = self.journal.prepare('recapture', plan.target_identity,
                                         original=original, backup=backup, pending=pending)
            pending.replace(original)
            swapped=True
            conn.execute('UPDATE profiles SET revision=revision+1 WHERE identity=?',(plan.target_identity,))
            self.audit(conn,actor['name'],'RECAPTURE',target['name'],f'{len(templates)} samples; backup saved')
            self.journal.mark(conn, token)
            conn.commit()
        except Exception:
            conn.rollback()
            if swapped and backup is not None:
                with pending.open('wb') as output:
                    output.write(backup.read_bytes());output.flush();os.fsync(output.fileno())
                pending.replace(original)
            raise
        finally:
            conn.close()
            pending.unlink(missing_ok=True)
        with self.connect() as recovery:
            self.journal.recover(recovery)
        return backup

    def _source(self, identity):
        if identity == "Admin":
            path = self.legacy
            allowed = self.legacy.parent.resolve()
        elif re.fullmatch(r"person:[0-9a-f]{32}", identity):
            path = self.directory / (identity.split(":", 1)[1] + ".npz")
            allowed = self.directory.resolve()
        else:
            raise ValueError("Invalid profile identity.")
        if path.resolve().parent != allowed or path.is_symlink():
            raise ValueError("Profile path is invalid.")
        return path

    def _backup_dir(self, identity):
        if identity=='Admin':key='admin'
        elif re.fullmatch(r'person:[0-9a-f]{32}',identity):key=identity.split(':')[1]
        else:raise ValueError('Invalid profile identity.')
        directory=self.directory.parent/'backups'/key
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.directory.parent.resolve()):
            raise ValueError('Invalid backup path.')
        return directory

    @serialized
    def mutate(self, plan, actor_identity):
        # Check the final permissions in the same transaction as the database write.
        with self.connect() as recovery:
            self.journal.recover(recovery)
        conn = self.connect()
        token = None
        pending = original = None
        backup_moves = []
        try:
            conn.execute("BEGIN IMMEDIATE")
            actor = conn.execute("SELECT * FROM profiles WHERE identity=?", (actor_identity,)).fetchone()
            target = conn.execute("SELECT * FROM profiles WHERE identity=?", (plan.target_identity,)).fetchone()
            if actor is None or not actor["active"] or actor["role"] not in ("ADMIN", "SUPERVISOR"):
                raise ValueError("Management access denied.")
            if plan.actor_role and actor["role"] != plan.actor_role:
                raise ValueError("Your access level changed. Run the command again.")
            if target is None or target["revision"] != plan.revision:
                raise ValueError("Profile changed. Run the command again.")
            if actor["role"] == "SUPERVISOR":
                if not plan.sudo or target["role"] not in ("USER", "OPERATOR"):
                    raise ValueError("SUPERVISOR may manage only USER and OPERATOR profiles with sudo.")
                if plan.kind == "role" and plan.value not in ("USER", "OPERATOR"):
                    raise ValueError("SUPERVISOR cannot assign this role.")
            if target["identity"] == actor_identity and (plan.kind in ("remove", "disable") or
                                                         (plan.kind == "role" and plan.value != "ADMIN")):
                raise ValueError("You cannot remove, disable or demote your active manager profile.")
            if target["role"] == "ADMIN" and target["active"] and (plan.kind in ("remove", "disable") or
                                                                 (plan.kind == "role" and plan.value != "ADMIN")):
                total = conn.execute("SELECT COUNT(*) FROM profiles WHERE role='ADMIN' AND active=1").fetchone()[0]
                if total <= 1:
                    raise ValueError("At least one active ADMIN must remain.")
            if plan.kind == "name":
                value = clean_name(plan.value)
                key = name_key(value)
                if conn.execute("SELECT 1 FROM profiles WHERE name_key=? AND identity<>?", (key, plan.target_identity)).fetchone():
                    raise ValueError("This name is already registered.")
                conn.execute("UPDATE profiles SET name=?,name_key=?,revision=revision+1 WHERE identity=?",
                             (value, key, plan.target_identity))
            elif plan.kind == "field":
                values = clean_fields(plan.value.split("+"))
                conn.execute("UPDATE profiles SET fields=?,revision=revision+1 WHERE identity=?",
                             (json.dumps(values), plan.target_identity))
            elif plan.kind == "role":
                if plan.value not in ROLES:
                    raise ValueError("Invalid role.")
                conn.execute("UPDATE profiles SET role=?,revision=revision+1 WHERE identity=?", (plan.value, plan.target_identity))
            elif plan.kind in ("enable", "disable"):
                conn.execute("UPDATE profiles SET active=?,revision=revision+1 WHERE identity=?",
                             (int(plan.kind == "enable"), plan.target_identity))
            elif plan.kind == "remove":
                original = self._source(plan.target_identity)
                pending = original.with_suffix(".removing")
                if pending.exists():
                    raise ValueError("A removal is already pending for this profile.")
                backup_dir=self._backup_dir(plan.target_identity)
                moves = [(original, pending)]
                for backup_path in backup_dir.glob('*.npz'):
                    if backup_path.is_symlink() or backup_path.resolve().parent != backup_dir.resolve():
                        raise ValueError('Invalid backup file path.')
                    removal = backup_path.with_suffix('.removing')
                    if removal.exists():
                        raise ValueError('A backup removal is already pending.')
                    moves.append((backup_path, removal))
                token = self.journal.prepare('remove', plan.target_identity, moves=moves)
                for source, removal in moves:
                    source.rename(removal)
                    if source != original:
                        backup_moves.append((source, removal))
                conn.execute("DELETE FROM profiles WHERE identity=?", (plan.target_identity,))
                conn.execute("DELETE FROM login_sessions WHERE identity=?", (plan.target_identity,))
            else:
                raise ValueError("Invalid edit action.")
            self.audit(conn, actor["name"], plan.kind.upper(), target["name"], str(plan.value or ""))
            if token is not None:
                self.journal.mark(conn, token)
            conn.commit()
        except Exception:
            conn.rollback()
            for source,removal in reversed(backup_moves):
                if removal.exists() and not source.exists():removal.rename(source)
            if pending is not None and pending.exists() and original is not None and not original.exists():
                pending.rename(original)
            raise
        finally:
            conn.close()
        with self.connect() as recovery:
            self.journal.recover(recovery)
        return f"{plan.kind.upper()} completed for {target['name']}."
