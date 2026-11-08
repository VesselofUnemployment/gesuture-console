import _bootstrap
from types import SimpleNamespace
import unittest
from gesture_permissions import can_track_hands, can_control_desktop, can_launch_gesture_apps
from recognition_presence import RecognitionPresence


class GesturePermissionsTests(unittest.TestCase):
    def test_role_matrix_and_unknown_roles_fail_closed(self):
        expected={'USER':(False,False,False),'OPERATOR':(True,False,False),
                  'SUPERVISOR':(True,True,False),'ADMIN':(True,True,True),'OTHER':(False,False,False)}
        for role,row in expected.items():
            for active in (True,False):
                with self.subTest(role=role,active=active):
                    person=SimpleNamespace(role=role,active=active)
                    actual=tuple(gate(person) for gate in
                        (can_track_hands,can_control_desktop,can_launch_gesture_apps))
                    self.assertEqual(actual,row if active else (False,False,False))
        self.assertFalse(can_track_hands(None));self.assertFalse(can_control_desktop(None))
        self.assertFalse(can_launch_gesture_apps(None))

    def test_grace_expires_and_user_or_multiface_immediately_revokes_permissions(self):
        supervisor=SimpleNamespace(identity='s',role='SUPERVISOR',active=True,revision=1)
        user=SimpleNamespace(identity='u',role='USER',active=True,revision=1)
        profiles={'s':supervisor,'u':user};presence=RecognitionPresence()
        presence.observe(supervisor,10.,profiles)
        self.assertTrue(can_control_desktop(presence.current(12.99,profiles)))
        self.assertFalse(can_track_hands(presence.current(13.,profiles)))
        presence.observe(supervisor,14.,profiles)
        presence.observe(user,14.1,profiles)
        self.assertFalse(can_track_hands(presence.current(14.1,profiles)))
        presence.observe(supervisor,15.,profiles)
        presence.observe(None,15.1,profiles,unsafe=True)
        self.assertFalse(can_control_desktop(presence.current(15.1,profiles)))


if __name__=='__main__':unittest.main()
