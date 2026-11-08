"""Shared role gates for local hand tracking and desktop actions."""


def can_track_hands(person):
    return bool(person is not None and person.active and
                person.role in ('OPERATOR','SUPERVISOR','ADMIN'))


def can_control_desktop(person):
    return bool(person is not None and person.active and
                person.role in ('SUPERVISOR','ADMIN'))


def can_launch_gesture_apps(person):
    return bool(person is not None and person.active and person.role=='ADMIN')
