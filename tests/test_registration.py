"""Test enrollment and identity rules with synthetic records."""
import _bootstrap  # Resolve application imports from any working directory.
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import unittest
import numpy as np

from person_store import (Person, role_for_code, save_person, load_people, match_person, clean_fields,
                          ensure_new_person)
from register_ui import CaptureSession
from vision_hud import AdminTypewriter, BLUE, RED, demo_frame, draw_hud


def vector(index):
    value = np.zeros(128, np.float32)
    value[index] = 1
    return value


class FakeRecognizer:
    def __init__(self, index=0):
        self.index = index
    def alignCrop(self, frame, face):
        return frame[:112, :112].copy()
    def feature(self, aligned):
        return vector(self.index)


class RegistrationTests(unittest.TestCase):
    def test_access_codes_and_invalid_code(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'codes.json'
            config = {'version': 1, 'algorithm': 'pbkdf2_sha256', 'iterations': 100000, 'roles': {}}
            for index, role in enumerate(('OPERATOR', 'SUPERVISOR', 'ADMIN')):
                salt = bytes([index+1])*16
                digest = hashlib.pbkdf2_hmac('sha256', ('test-'+role).encode(), salt, 100000)
                config['roles'][role] = {'salt': salt.hex(), 'digest': digest.hex()}
            path.write_text(json.dumps(config), encoding='utf-8')
            self.assertEqual(role_for_code('', path), 'USER')
            for role in config['roles']:
                self.assertEqual(role_for_code('test-'+role, path), role)
            with self.assertRaises(ValueError):
                role_for_code('wrong', path)

    def test_person_roundtrip_and_existing_records(self):
        with TemporaryDirectory() as tmp:
            directory, legacy = Path(tmp)/'people', Path(tmp)/'absent.npz'
            path = save_person('Çağrı', ('Software', 'Mechanics'), 'OPERATOR', [vector(0)]*3,
                               directory, legacy)
            before = path.read_bytes()
            people = load_people(directory, legacy)
            self.assertEqual(people[0].name, 'Çağrı')
            self.assertEqual(people[0].role, 'OPERATOR')
            self.assertEqual(people[0].fields, ('Software', 'Mechanics'))
            with self.assertRaises(ValueError):
                save_person('ÇAĞRI', ('Design',), 'ADMIN', [vector(1)]*3, directory, legacy)
            with self.assertRaises(ValueError):
                save_person('Another_name', ('Robotics',), 'ADMIN', [vector(0)]*3, directory, legacy)
            with self.assertRaises(RuntimeError):
                save_person('Incomplete', (), 'USER', [vector(1)]*2, directory, legacy)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(list(directory.glob('*.npz'))), 1)

    def test_three_manual_samples_and_quality_checks(self):
        with TemporaryDirectory() as tmp:
            directory, legacy = Path(tmp)/'people', Path(tmp)/'absent.npz'
            session = CaptureSession('Deniz', ('Electronics',), 'SUPERVISOR', directory, legacy)
            frame = np.random.default_rng(0).integers(70, 180, (480, 640, 3), dtype=np.uint8)
            face = np.zeros((1, 15), np.float32)
            face[0, :4] = (100, 100, 150, 150)
            model = FakeRecognizer()
            session.capture(frame, None, model)
            session.capture(frame, np.repeat(face, 2, axis=0), model)
            session.capture(np.zeros_like(frame), face, model)
            self.assertEqual(len(session.samples), 0)
            session.capture(frame, face, model)
            session.capture(frame, face, model)
            self.assertFalse(directory.exists())
            model.index = 1
            session.capture(frame, face, model)
            self.assertEqual(len(session.samples), 2)
            model.index = 0
            session.capture(frame, face, model)
            self.assertEqual(len(session.samples), 3)
            self.assertEqual(len(load_people(directory, legacy)), 1)
            session.capture(frame, face, model)
            self.assertEqual(len(list(directory.glob('*.npz'))), 1)

    def test_distinct_identity_unknown_and_ambiguous(self):
        a = Person('person:a', 'Deniz', ('Software',), 'USER', np.stack([vector(0)]*3))
        b = Person('person:b', 'Ece', ('Design',), 'ADMIN', np.stack([vector(1)]*3))
        self.assertEqual(match_person(vector(0), [a,b])[0].identity, a.identity)
        self.assertEqual(match_person(vector(1), [a,b])[0].identity, b.identity)
        self.assertIsNone(match_person(vector(2), [a,b])[0])
        self.assertIsNone(match_person(vector(0)+vector(1), [a,b])[0])
        frame, face = demo_frame()
        profiles = {p.identity:p for p in (a,b)}
        writer = AdminTypewriter(profiles=profiles)
        self.assertEqual(writer.update([(face,a.identity,BLUE,.8)],frame.shape,now=0),[1])
        self.assertEqual(writer.update([(face,a.identity,BLUE,.8)],frame.shape,now=.17),[2])
        self.assertEqual(writer.update([(face,b.identity,BLUE,.8)],frame.shape,now=.18),[1])
        self.assertEqual(writer.update([(face,'Unknown',RED,.2)],frame.shape,now=.19),[None])
        self.assertEqual(writer.update([(face,b.identity,BLUE,.8)],frame.shape,now=.20),[1])
        one = draw_hud(frame,[(face,'Unknown',RED,.2)],[30],profiles)
        two = draw_hud(frame,[(face,'Unknown',RED,.2)],None,profiles)
        self.assertTrue(np.array_equal(one,two))
        copy = frame.copy()
        for profile in profiles.values():
            image = draw_hud(frame,[(face,profile.identity,BLUE,.8)],[30],profiles)
            self.assertFalse(np.array_equal(image,frame))
        self.assertTrue(np.array_equal(frame,copy))
        for shape in [(120,120,3),(480,640,3),(720,1280,3)]:
            draw_hud(np.zeros(shape,np.uint8),[(face,b.identity,BLUE,.8)],[30],profiles)

    def test_mirrored_preview_and_resize_preserve_source(self):
        from camera_view import (registration_preview, mirrored_results, fitted_size, letterbox)
        frame = np.zeros((480, 640, 3), np.uint8)
        frame[:, :80] = (20, 40, 200)
        frame[:, -80:] = (200, 40, 20)
        original = frame.copy()
        self.assertTrue(np.array_equal(registration_preview(frame, None), frame[:, ::-1]))
        face = np.zeros(15, np.float32)
        face[:4] = (70, 100, 120, 150)
        source_face = face.copy()
        results = mirrored_results([(face, 'Admin', BLUE, .8)], 640)
        self.assertEqual(results[0][0][0], 450)
        self.assertEqual(results[0][1:], ('Admin', BLUE, .8))
        boxed = registration_preview(frame, np.asarray([face]))
        self.assertTrue(np.array_equal(boxed[100,450],np.array((238,196,118),np.uint8)))
        self.assertTrue(np.array_equal(frame,original))
        self.assertTrue(np.array_equal(face,source_face))
        self.assertEqual(fitted_size(frame.shape,1920,1080),(1440,1080))
        for width,height in [(1,1),(320,240),(800,200),(1920,1080),(500,900)]:
            fw,fh = fitted_size(frame.shape,width,height)
            self.assertLessEqual(fw,width)
            self.assertLessEqual(fh,height)
            output = letterbox(frame,width,height)
            self.assertEqual(output.shape,(height,width,3))
            if fw>10 and fh>10:
                self.assertLess(abs(fw/fh-640/480),.02)
        self.assertTrue(np.array_equal(frame,original))

    def test_field_selection(self):
        self.assertEqual(clean_fields([]), ('Unspecified',))
        self.assertEqual(clean_fields(['Mechanics','Software']), ('Software','Mechanics'))
        with self.assertRaises(ValueError):
            clean_fields(['Unspecified','Software'])


if __name__ == '__main__':
    unittest.main()
