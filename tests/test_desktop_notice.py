import _bootstrap
import unittest
from person_store import Person
from desktop_notice import NoticePresence, appearance


def person(identity='a',name='admin',role='ADMIN',**kwargs):
    return Person(identity,name,('Software',),role,None,**kwargs)


class NoticeTests(unittest.TestCase):
    def test_stable_identity_welcomes_once(self):
        policy=NoticePresence();p=person()
        self.assertEqual(sum(policy.observe(p,t/10) for t in range(100)),1)

    def test_no_unknown_or_disabled_profile_welcome(self):
        for p in (None,person(active=False)):
            policy=NoticePresence()
            self.assertFalse(any(policy.observe(p,t/10) for t in range(20)))

    def test_brief_loss_does_not_replay(self):
        policy=NoticePresence();p=person()
        for t in range(5):policy.observe(p,t/10)
        for t in range(5,25):policy.observe(None,t/10)
        self.assertFalse(any(policy.observe(p,t/10) for t in range(25,40)))

    def test_five_seconds_loss_rearms_even_when_poll_was_paused(self):
        policy=NoticePresence();p=person()
        for t in range(5):policy.observe(p,t/10)
        self.assertEqual(sum(policy.observe(p,5.5+t/10) for t in range(10)),1)

    def test_new_identity_and_metadata_use_current_username(self):
        for changed in (person('b','Deniz','USER'),person(name='Yeni'),person(role='SUPERVISOR'),person(revision=1)):
            policy=NoticePresence();p=person()
            for t in range(5):policy.observe(p,t/10)
            self.assertEqual(sum(policy.observe(changed,.5+t/10) for t in range(10)),1)
            self.assertEqual(policy.current,policy.key(changed))

    def test_alternating_results_and_frame_gaps_never_confirm(self):
        policy=NoticePresence()
        self.assertFalse(any(policy.observe(person(str(t%2)),t/10) for t in range(20)))
        policy=NoticePresence()
        self.assertFalse(any(policy.observe(person(),t) for t in range(20)))

    def test_fade_boundaries_and_smooth_entrance(self):
        self.assertEqual(appearance(0),(0.,7.,False))
        self.assertEqual(appearance(.35),(1.,0.,False))
        self.assertEqual(appearance(3.5),(1.,0.,False))
        self.assertEqual(appearance(4),(0.,0.,True))
        self.assertTrue(0<appearance(.15)[0]<1)
        self.assertTrue(0<appearance(3.75)[0]<1)


if __name__=='__main__':unittest.main()
