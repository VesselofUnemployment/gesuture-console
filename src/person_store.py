"""Local profiles and enrollment password verification."""
from project_paths import PROJECT_ROOT
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import hmac
import importlib
import json
import re
import os
import unicodedata
import uuid
from storage_runtime import profile_lock

import numpy as np

BASE = PROJECT_ROOT
DATA = BASE / "data"
PEOPLE = DATA / "people"
ACCESS_FILE = DATA / "access_codes.json"
ROLES = ("USER", "OPERATOR", "SUPERVISOR", "ADMIN")
FIELDS = ("Software", "Mechanics", "Electronics", "Robotics", "Design", "Other", "Unspecified")
SUPPORTED = set(chr(n) for n in range(32, 127)) | set("ÇçĞğİıÖöŞşÜü")


@dataclass
class Person:
    identity: str
    name: str
    fields: tuple
    role: str
    templates: np.ndarray
    created_at: str = ""
    last_login: str = ""
    active: bool = True
    revision: int = 0


def clean_name(value):
    name = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(name) <= 24:
        raise ValueError("Enter a name with 1 to 24 characters.")
    if any(char.isspace() for char in name):
        raise ValueError("Names cannot contain spaces. Use an underscore instead.")
    if any(char not in SUPPORTED or not (char.isalnum() or char in "_-") for char in name):
        raise ValueError("Use Latin/Turkish letters, numbers, underscores or hyphens.")
    if not any(char.isalpha() for char in name):
        raise ValueError("Your name must contain a letter.")
    return name


def clean_fields(values):
    if set(values) - set(FIELDS):
        raise ValueError("Unknown field selection.")
    selected = tuple(field for field in FIELDS if field in values)
    if not selected:
        return ("Unspecified",)
    if "Unspecified" in selected and len(selected) > 1:
        raise ValueError("Choose Unspecified alone or select your fields.")
    return selected


def name_key(name):
    return clean_name(name).casefold().replace("\u0307", "").replace("ı", "i")


def read_access_config(path=ACCESS_FILE):
    with Path(path).open(encoding="utf-8") as source:
        return json.load(source)


def find_access_role(code, config):
    if not isinstance(config, dict) or config.get("version") != 1 or config.get("algorithm") != "pbkdf2_sha256":
        raise ValueError("Access configuration is invalid.")
    rounds = config.get("iterations")
    if not isinstance(rounds, int) or not 100_000 <= rounds <= 2_000_000:
        raise ValueError("Access configuration is invalid.")
    roles = config.get('roles')
    if not isinstance(roles, dict):
        raise ValueError('Access configuration is invalid.')
    found = None
    for role in ROLES[1:]:
        item = roles.get(role)
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ('salt', 'digest')):
            raise ValueError('Access configuration is invalid.')
        salt, expected = bytes.fromhex(item["salt"]), bytes.fromhex(item["digest"])
        if len(salt) != 16 or len(expected) != 32:
            raise ValueError("Access configuration is invalid.")
        actual = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, rounds)
        if hmac.compare_digest(actual, expected):
            found = role
    return found


def role_for_code(code, path=ACCESS_FILE):
    # Only an empty password selects USER. Reject incorrect passwords.
    if not code:
        return "USER"
    found = find_access_role(code, read_access_config(path))
    if found is None:
        raise ValueError("Incorrect access code. Try again or leave it empty for USER.")
    return found


def read_person(path, legacy=False):
    enrollment = importlib.import_module("face_enrollment")
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Profile symlinks are not supported.")
    if legacy:
        person = Person("Admin", "Admin", ("Software", "Mechanics"), "ADMIN", enrollment.load_admin(path))
    else:
        if not re.fullmatch(r"[0-9a-f]{32}", path.stem):
            raise ValueError("Invalid person record filename.")
        with np.load(path, allow_pickle=False) as record:
            if record["version"].item() != 2 or record["model"].item() != "sface_2021dec":
                raise ValueError("Person record version is invalid.")
            name = clean_name("_".join(str(record["name"].item()).split()))
            fields = clean_fields(record["fields"].tolist())
            role = str(record["role"].item())
            if role not in ROLES:
                raise ValueError("Person role is invalid.")
            templates = enrollment.validate_templates(record["templates"], expected_count=3).copy()
            created = str(record["created_at"].item()) if "created_at" in record else ""
        person = Person("person:" + path.stem, name, fields, role, templates, created_at=created)
    person.templates.setflags(write=False)
    return person


def load_raw_people(directory=PEOPLE, legacy=None):
    enrollment = importlib.import_module("face_enrollment")
    legacy = Path(legacy) if legacy is not None else enrollment.ADMIN_FILE
    result = [read_person(legacy, True)] if legacy.is_file() else []
    return result + [read_person(path) for path in sorted(Path(directory).glob("*.npz"))]


def load_people(directory=PEOPLE, legacy=None):
    from profile_store import ProfileRepository
    return ProfileRepository(directory, legacy).people()


def ensure_new_person(name, samples, people, threshold=0.65):
    if any(name_key(person.name) == name_key(name) for person in people):
        raise ValueError("This name is already registered. Existing records are preserved.")
    if samples is not None:
        for person in people:
            similarities = np.asarray(samples) @ person.templates.T
            # Reject a new profile that matches an already enrolled face.
            if float(np.max(similarities)) >= threshold:
                raise ValueError("This face matches an existing profile. No new record was saved.")


def save_person(name, fields, role, samples, directory=PEOPLE, legacy=None):
    enrollment = importlib.import_module("face_enrollment")
    name, fields = clean_name(name), clean_fields(fields)
    if role not in ROLES:
        raise ValueError("Invalid role.")
    templates = enrollment.validate_templates(np.asarray(samples), expected_count=3)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with profile_lock(directory.parent):
        # Recognition can exclude corrupt sources, but enrollment must not
        # accept a possible duplicate while any source is unreadable.
        load_raw_people(directory, legacy)
        # Recheck under the lock for enrollments made during this session.
        from profile_store import ProfileRepository
        repository = ProfileRepository(directory, legacy)
        people = repository.people()
        if repository.record_errors:
            raise ValueError('Profile records need attention before registering another person.')
        ensure_new_person(name, templates, people)
        path = directory / (uuid.uuid4().hex + ".npz")
        pending = path.with_suffix(".pending")
        try:
            with pending.open("xb") as output:
                np.savez_compressed(output, version=np.array(2), model=np.array("sface_2021dec"),
                                    name=np.array(name), fields=np.array(fields), role=np.array(role),
                                    created_at=np.array(datetime.now(timezone.utc).isoformat(timespec="seconds")),
                                    templates=templates)
                output.flush()
                os.fsync(output.fileno())
            # Windows rename preserves existing files and exposes only complete NPZ files.
            pending.rename(path)
        finally:
            pending.unlink(missing_ok=True)
        return path


def match_person(feature, people, threshold=0.65, margin=0.05, details=None):
    enrollment = importlib.import_module("face_enrollment")
    query = enrollment.normalize(feature)
    ranking = sorted(((float(np.max(np.clip(person.templates @ query, -1, 1))), person)
                      for person in people if person.active), key=lambda item: item[0], reverse=True)
    if not ranking:
        return None, None
    best, person = ranking[0]
    # Reject matches when the two best identities are too close.
    difference = best-ranking[1][0] if len(ranking)>1 else None
    ambiguous = difference is not None and difference < margin
    if details is not None:
        details.update(margin=difference, ambiguous=ambiguous)
    return (person if best >= threshold and not ambiguous else None), best
