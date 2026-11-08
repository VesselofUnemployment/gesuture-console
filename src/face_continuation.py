"""Bounded recognition hysteresis on fresh, geometrically continuous faces.

No new embeddings, models or images are stored. A continuation can never seed
a new identity or extend its own one-second strong-match anchor.
"""
import math
import time
import numpy as np
from person_store import match_person


class FaceContinuation:
    MAX_AGE = 1.0
    MAX_GAP = .25
    SCORE_DROP = .05
    FLOOR = .60

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset()

    def _clear(self):
        self.token = self.box = self.anchor = self.last = None

    def reset(self):
        self._clear()
        self.config = None
        self.single = self.fresh = False
        self.stamp = self.now = None
        self.decision = {}

    @staticmethod
    def profile_token(person):
        return person.identity, person.role, person.revision, person.active

    def prepare(self, count, stamp, threshold, generation, now=None):
        now = self.clock() if now is None else now
        config = (float(threshold), generation)
        fresh = 0 <= now-stamp <= .6
        if (count != 1 or not fresh or config != self.config or
                (self.last is not None and not 0 < stamp-self.last <= self.MAX_GAP) or
                (self.anchor is not None and not 0 <= now-self.anchor <= self.MAX_AGE)):
            self._clear()
        self.config = config
        self.single, self.fresh = count == 1, fresh
        self.stamp, self.now = stamp, now

    def continuous(self, face, people):
        if not self.single or not self.fresh or self.token is None or self.box is None:
            return False
        person = next((p for p in people if p.identity == self.token[0]), None)
        if person is None or not person.active or self.profile_token(person) != self.token:
            return False
        box = np.asarray(face[:4], float)
        old = self.box
        if not np.isfinite(box).all() or np.any(box[2:] <= 0):
            return False
        ratio = box[2:]/old[2:]
        shift = np.linalg.norm((box[:2]+box[2:]/2)-(old[:2]+old[2:]/2))
        return bool(.5 <= ratio[0] <= 2 and .7 <= ratio[1] <= 1.45
                    and shift <= .35*max(old[3],box[3]))

    def match(self, feature, face, people, threshold):
        self.fresh = bool(self.fresh and self.stamp is not None and 0 <= self.clock()-self.stamp <= .6)
        if self.anchor is not None and not 0 <= self.clock()-self.anchor <= self.MAX_AGE:
            self._clear()
        eligible = self.continuous(face, people)
        effective = min(threshold, max(self.FLOOR, threshold-self.SCORE_DROP)) if eligible else threshold
        details = {}
        person, score = match_person(feature, people, effective, details=details)
        continued = bool(person is not None and score < threshold)
        # A weak match to another identity never benefits from this context.
        if continued and (not eligible or self.profile_token(person) != self.token):
            person = None
            continued = False
            effective = threshold
        reason = ('MATCHED_CONTINUATION' if continued else 'MATCHED') if person else (
            'NO_ACTIVE_PROFILES' if score is None else
            'AMBIGUOUS' if details.get('ambiguous') else 'BELOW_THRESHOLD')
        self.decision = dict(reason=reason, initial_threshold=threshold,
                             effective_threshold=effective, identity_margin=details.get('margin'))
        if person is not None and self.single and self.fresh:
            if not continued:
                self.anchor = self.stamp
            self.token = self.profile_token(person)
            self.box = np.asarray(face[:4], float).copy()
            self.last = self.stamp
        else:
            self._clear()
        return person, score
