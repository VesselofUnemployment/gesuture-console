"""Commands for the app's management terminal."""
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from person_store import clean_name, clean_fields, FIELDS, ROLES, role_for_code, ACCESS_FILE
from profile_store import display_time
from vision_runtime import VisionDiagnostics
from activity_feed import ActivityFeed
from soft_sounds import SoftSounds
from recognition_presence import LOSS_GRACE

HELP = """USER MANAGEMENT
  user base                          List all registered profiles
  user NAME info                     Show name, field, role, dates and face samples
  user NAME stats                    Login count, first seen today, current session
  user NAME recapture                Refresh face samples in this window
  user edit NAME name NEW_NAME        Rename a profile (no spaces)
  user edit NAME field FIELD          Change field(s)
  user edit NAME role ROLE            Change access level
  user NAME disable                  Disable recognition, keep the face record
  user NAME enable                   Enable recognition again
  user NAME remove                   Delete profile and face record after y/N

VISION COMMANDS
  vision status                      Camera, FPS, processing time, frame age, models
  vision debug                       Latest per-face score, lighting, blur and reason
  vision debug on                     Show debug information on the camera
  vision debug off                    Hide debug information
  vision threshold                   Show the current match threshold
  vision threshold VALUE             Set threshold for this run (ADMIN only)
  vision sound [on|off]               Quiet scan / verified / panel sounds
  vision hands [on|off]               Hand tracking and gestures (OPERATOR or above)
  vision apps [on|off]                ADMIN face + number/fist/session app shortcuts

SESSION COMMANDS
  status                             Show service, profile count and lock settings
  whoami                             Show the current panel owner's profile
  logs                               Show the latest 40 activity records
  watch                              Follow live events; Ctrl+C returns to prompt
  help                               Show every command and these examples
  clear                              Clear the terminal output
  lock                               Lock this panel immediately
  exit                               Close the panel and return to the camera
  terminate connection               Release camera and quit Vision Control (ADMIN)

EXAMPLES (replace Deniz with a registered name)
  user Deniz info
  user Deniz stats
  user Deniz recapture
  vision debug on
  vision threshold 0.65
  vision sound off
  vision hands off
  watch
  terminate connection
  user edit Deniz name Deniz_2
  user edit Deniz field Software+Mechanics
  user edit Deniz role OPERATOR
  user Deniz disable
  user Deniz enable
  user Deniz remove

SUPERVISOR CHANGES (password required for every command)
  sudo user edit NAME name NEW_NAME
  sudo user edit NAME field FIELD
  sudo user edit NAME role ROLE
  sudo user NAME disable
  sudo user NAME enable
  sudo user NAME remove
  sudo user NAME recapture
  Example: sudo user edit Deniz field Robotics

FIELDS  Software / Mechanics / Electronics / Robotics / Design / Other / Unspecified
        Combine fields with +, e.g. Software+Mechanics. Unspecified must stand alone.
ROLES   USER / OPERATOR / SUPERVISOR / ADMIN

ADMIN can change profiles directly; sudo is optional with the ADMIN code.
SUPERVISOR must use sudo and their SUPERVISOR code for each change;
only USER / OPERATOR profiles may be managed and only those roles assigned.
Recapture: CAPTURE / K for each pose, then SAVE. Old samples are backed up.
The original manager must be recognized before SAVE; an expired SUPERVISOR
session also needs its code again. CANCEL / Esc preserves the old samples.
Your active manager profile cannot be removed, disabled or demoted.
Remove: enter y or yes to confirm; Enter / n cancels.
Names: no spaces, 1-24 characters; underscores and hyphens are allowed.

KEYS    Up / Down: history    Tab: completion    Ctrl+C: cancel prompt / stop watch
        Esc / BACK TO CAMERA / exit: return to camera    F11: full screen
Use the mouse wheel or scroll bar to read earlier output.
The camera continues recognition while this panel is open.
Brief face recognition losses keep authority for 3 seconds.
The panel locks after 5 seconds without its owner recognized.
To unlock, return to the camera and press ADMIN PANEL.
Only a currently recognized ADMIN / SUPERVISOR can open a panel.
Sound settings apply to this run."""


def registered_text(person, date_only=False):
    # Tie the special label to the original profile ID.
    if person.identity == "Admin":
        return "WAS ALWAYS HERE"
    return display_time(person.created_at, date_only)


def table(headers, rows):
    rows = [[str(x) for x in row] for row in rows]
    widths = [max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
              for i, header in enumerate(headers)]
    def line(row):
        return "  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()
    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(row) for row in rows])


class SessionGate:
    """Bind the management panel to one recognized person at a time."""
    def __init__(self, timeout=5.0, clock=time.monotonic):
        self.timeout, self.clock = timeout, clock
        self.owner = None
        self.candidate = None
        self.candidate_at = float('-inf')
        self.last_seen = float('-inf')
        self.reason = "Session locked. Look at the camera and press ADMIN PANEL."

    def observe(self, person, now=None, unsafe=False):
        now = self.clock() if now is None else now
        if unsafe or person is not None:
            self.candidate = person if not unsafe and person.active and person.role in ("ADMIN", "SUPERVISOR") else None
            self.candidate_at = now if self.candidate else float('-inf')
        self.current_candidate(now)
        if self.owner:
            if unsafe or (person is not None and person.identity != self.owner):
                self.lock("Session locked: another face is present.")
            elif person and person.identity == self.owner and self.candidate:
                self.last_seen = now
            self.valid(self.owner, now)

    def current_candidate(self, now=None):
        now = self.clock() if now is None else now
        if not 0 <= now-self.candidate_at < LOSS_GRACE:
            self.candidate = None
        return self.candidate

    def open(self, now=None):
        now = self.clock() if now is None else now
        if not self.current_candidate(now):
            raise ValueError("Look at the camera until your manager profile is recognized.")
        self.owner = self.candidate.identity
        self.last_seen = self.candidate_at
        self.reason = ""
        return self.owner

    def valid(self, identity, now=None):
        now = self.clock() if now is None else now
        if self.owner and now - self.last_seen >= self.timeout:
            self.lock(f"Session locked: owner absent for {self.timeout:g} seconds.")
        return self.owner is not None and self.owner == identity

    def lock(self, reason="Session locked. Look at the camera and press ADMIN PANEL."):
        self.owner, self.reason = None, reason


class PresenceTracker:
    def __init__(self, repo, gap=10.0):
        self.repo, self.gap, self.seen = repo, gap, {}
        self.sessions = {}

    def observe(self, identities, now):
        wall = datetime.now(timezone.utc).isoformat(timespec='seconds')
        self.expire(now)
        for identity in set(identities):
            if identity not in self.sessions:
                session_id=self.repo.mark_login(identity)
                if session_id:
                    self.sessions[identity]=dict(id=session_id,start=now,persisted=now,wall=wall)
            session=self.sessions.get(identity)
            if session:
                session['wall']=wall
                if now-session['persisted']>=5:
                    self.repo.touch_login(session['id'],wall)
                    session['persisted']=now
            self.seen[identity]=now

    def expire(self, now):
        for identity in list(self.sessions):
            if now-self.seen.get(identity,float('-inf'))>=self.gap:
                session=self.sessions.pop(identity)
                self.repo.end_login(session['id'],session['wall'])

    def current(self, identity, now=None):
        now=time.monotonic() if now is None else now
        session=self.sessions.get(identity)
        if not session or now-self.seen.get(identity,float('-inf'))>=self.gap:
            return None
        return max(0,now-session['start'])

    def close(self):
        for session in self.sessions.values():
            self.repo.end_login(session['id'],session['wall'])
        self.sessions.clear()


def duration_text(seconds):
    seconds=max(0,int(seconds))
    return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'


@dataclass(frozen=True)
class MutationPlan:
    kind: str
    target_identity: str
    target_name: str
    revision: int
    value: str = ""
    sudo: bool = False
    actor_role: str = ""


@dataclass
class Result:
    text: str = ""
    action: str = ""
    request: str = ""
    plan: MutationPlan = None
    changed: bool = False


class CommandEngine:
    def __init__(self, repo, actor_identity, guard, access_file=ACCESS_FILE, threshold=0.65, vision=None, presence=None, feed=None, sounds=None):
        self.repo, self.actor_identity, self.guard = repo, actor_identity, guard
        self.access_file, self.threshold = access_file, threshold
        self.vision = vision if vision is not None else VisionDiagnostics(threshold=threshold)
        self.presence = presence
        self.feed = feed if feed is not None else ActivityFeed()
        self.sounds = sounds if sounds is not None else SoftSounds()
        self.pending = None
        self.pending_request = ""

    def actor(self):
        if not self.guard(self.actor_identity):
            self.cancel()
            raise ValueError("Session locked. Look at the camera and press ADMIN PANEL.")
        actor = self.repo.get(self.actor_identity)
        if not actor or not actor.active or actor.role not in ("ADMIN", "SUPERVISOR"):
            self.cancel()
            raise ValueError("Management access is no longer available.")
        return actor

    def cancel(self):
        self.pending = None
        self.pending_request = ""

    def begin(self, command):
        self.cancel()
        actor = self.actor()
        parts = command.strip().split()
        if not parts:
            return Result()
        sudo = parts[0].lower() == "sudo"
        if sudo:
            parts = parts[1:]
        lower = [part.lower() for part in parts]
        if not parts:
            raise ValueError("Usage: sudo user ...")
        cmd = lower[0]
        if not sudo and lower == ['terminate', 'connection']:
            if actor.role != 'ADMIN':raise ValueError('Only ADMIN may terminate the connection.')
            return Result(action='terminate')
        if cmd == "vision" and not sudo:
            return self.vision_command(parts, actor)
        if cmd != "user":
            if sudo or len(parts) != 1:
                raise ValueError("Unknown command. Type help.")
            if cmd == "help": return Result(HELP)
            if cmd in ("clear", "lock", "exit"): return Result(action=cmd)
            if cmd == 'watch':return Result(action='watch')
            if cmd == "whoami": return Result(self.info(actor))
            if cmd == "status":
                people = self.repo.people()
                return Result(f"VISION SERVICE    ONLINE\nSESSION OWNER     {actor.name}\nACCESS LEVEL      {actor.role}\nREGISTERED        {len(people)}\nACTIVE            {sum(p.active for p in people)}\nMATCH THRESHOLD   {self.vision.snapshot()['threshold']:.2f}\nAUTO LOCK         5 seconds\nTIME ZONE         UTC+03:00")
            if cmd == "logs":
                rows = [[display_time(r['timestamp']), r['actor'], r['action'], r['target'], r['detail']]
                        for r in self.repo.logs()]
                return Result(table(["TIME", "ACTOR", "ACTION", "TARGET", "DETAIL"], rows))
            raise ValueError("Unknown command. Type help.")
        if lower == ["user", "base"] and not sudo:
            rows = [[p.name, " & ".join(p.fields), p.role, registered_text(p, True),
                     display_time(p.last_login), "ACTIVE" if p.active else "DISABLED"] for p in self.repo.people()]
            return Result(table(["NAME", "FIELD", "ROLE", "REGISTERED", "LAST LOGIN", "STATUS"], rows))
        if len(parts) == 3 and lower[2] in ("info", "stats") and not sudo:
            person=self.find(parts[1])
            return Result(self.info(person) if lower[2]=="info" else self.stats_text(person))
        if len(parts) == 5 and lower[1] == "edit" and lower[3] in ("name", "field", "role"):
            target, kind, value = self.find(parts[2]), lower[3], parts[4]
            if kind == "name": value = clean_name(value)
            elif kind == "role":
                value = value.upper()
                if value not in ROLES: raise ValueError("Role must be " + " / ".join(ROLES))
            else:
                lookup = {f.lower(): f for f in FIELDS}
                try: fields = tuple(lookup[f.lower()] for f in value.split('+'))
                except KeyError: raise ValueError("Unknown field. Type help for available fields.") from None
                value = '+'.join(clean_fields(fields))
        elif len(parts) == 3 and lower[2] in ("remove", "disable", "enable", "recapture"):
            target, kind, value = self.find(parts[1]), lower[2], ""
        else:
            raise ValueError("Invalid syntax. Type help. Names cannot contain spaces.")
        if actor.role == "SUPERVISOR":
            if not sudo: raise ValueError("Use sudo before changes as SUPERVISOR.")
            if target.role not in ("USER", "OPERATOR") or (kind == "role" and value not in ("USER", "OPERATOR")):
                raise ValueError("SUPERVISOR may manage only USER and OPERATOR profiles.")
        if target.identity == actor.identity and (kind in ("remove", "disable") or (kind == "role" and value != "ADMIN")):
            raise ValueError("You cannot remove, disable or demote your active manager profile.")
        plan = MutationPlan(kind, target.identity, target.name, target.revision, value, sudo, actor.role)
        if sudo:
            self.pending, self.pending_request = plan, "password"
            return Result(request="password", plan=plan)
        return self.ready(plan)

    def reply(self, value):
        actor = self.actor()
        plan, request = self.pending, self.pending_request
        self.cancel()
        if plan is None:
            raise ValueError("No command is pending.")
        if request == "password":
            try: verified = role_for_code(value, self.access_file) == actor.role
            except ValueError: verified = False
            if not verified:
                self.repo.log(actor.name, "SUDO_DENIED", plan.target_name)
                raise ValueError("Authentication failed. Command cancelled.")
            return self.ready(plan)
        if request == "confirm":
            if value.strip().lower() not in ("y", "yes"):
                return Result("Removal cancelled.")
            return self.apply(plan)
        raise ValueError("Invalid pending command.")

    def ready(self, plan):
        self.actor()
        if plan.kind == "remove":
            self.pending, self.pending_request = plan, "confirm"
            return Result(request="confirm", plan=plan)
        return self.apply(plan)

    def apply(self, plan):
        actor = self.actor()
        if plan.actor_role and plan.actor_role != actor.role:
            raise ValueError("Your access level changed. Run the command again.")
        if plan.kind == "recapture":
            self.repo.validate_recapture(plan,self.actor_identity)
            return Result(action="recapture",plan=plan)
        message = self.repo.mutate(plan, self.actor_identity)
        return Result(message, changed=True)

    def stats_text(self, person):
        stats=self.repo.stats(person.identity)
        current=self.presence.current(person.identity) if self.presence is not None else None
        values=(('NAME',person.name),('TOTAL LOGINS',stats['total']),('LOGINS TODAY',stats['today']),
                ('FIRST SEEN TODAY',display_time(stats['first_today'])),
                ('FIRST RECORDED',display_time(stats['first_recorded'])),
                ('CURRENT SESSION',duration_text(current) if current is not None else 'INACTIVE'),
                ('LEGACY LOGINS',stats['legacy']),('TIME ZONE','UTC+03:00'))
        return '\n'.join(f'{key:<18} {value}' for key,value in values)

    def vision_command(self, parts, actor):
        lower=[part.lower() for part in parts]
        if lower==['vision','status']:
            state=self.vision.snapshot()
            age='--' if state['age'] is None else f"{state['age']:.2f}s"
            w,h=state['resolution']
            rows=(('CAMERA',state['state']),('DEVICE',state['camera_index']),('RESOLUTION',f'{w} x {h}'),
                  ('PROCESSED FPS',f"{state['fps']:.1f}"),('PROCESSING',f"{state['processing_ms']:.1f}ms"),
                  ('FRAME AGE',age),('FRAMES',state['frames']),('FACES',len(state['faces'])),
                  ('THRESHOLD',f"{state['threshold']:.3f}"),('IDENTITY MARGIN','0.05'),
                  ('DEBUG OVERLAY','ON' if state['debug'] else 'OFF'),
                  ('MODELS','YuNet 2023mar / SFace 2021dec'),
                  ('FACE TRACKING',state['tracking']),('LANDMARKS',state['landmarks']),
                  ('MESH TIME',f"{state['tracking_ms']:.1f}ms"),('ANIMATION',state['animation']),
                  ('TRACKING ERROR',state['tracking_error'] or '--'),('ERROR',state['error'] or '--'))
            summary=self.vision.face_summary()
            rows += (('FACE WINDOW',str(summary['window_frames'])),('FACE REASONS',str(summary['reasons'])),
                     ('CONTINUE THRESHOLD',f"{min(state['threshold'],max(.60,state['threshold']-.05)):.3f} / max 1 second"))
            rows += (('QUIET SOUNDS','ON' if self.sounds.enabled else 'OFF'),)
            rows += (('HAND TRACKING',state['hands_state']),('HANDS',len(state['hands'])),
                     ('HAND TIME',f"{state['hands_ms']:.1f}ms"),
                     ('HAND GESTURES',', '.join(h['gesture'] for h in state['hands']) or '--'),
                     ('HAND NUMBERS',', '.join('--' if h['fingers'] is None else str(h['fingers']) for h in state['hands']) or '--'),
                     ('HAND ERROR',state['hands_error'] or '--'))
            rows += (('APP CONTROL','ON' if state['apps_enabled'] else 'OFF'),
                     ('APP SHORTCUTS',self.vision.app_shortcuts),
                     ('LAST APP ACTION',state['app_action'] or '--'))
            rows += (('CAMERA POWER','LOW / dark image' if state['camera_paused'] else 'NORMAL'),
                     ('DARK CHECK','4 per second' if state['camera_paused'] else 'Before each inference'),
                     ('FRAME LIGHT',f"{state['camera_light']['mean']:.1f} / 255"),
                     ('AUTHORITY GRACE','3 seconds'))
            rows += tuple((key.replace('_',' ').upper(),f'{value:.1f}ms')
                          for key,value in state.get('latency',{}).items())
            from runtime_lifecycle import process_resources
            rows += tuple((key.replace('_', ' ').upper(), str(value)) for key, value in process_resources().items())
            rows += tuple((key.replace('_', ' ').upper(), str(value)) for key, value in state.get('counters', {}).items())
            rows += tuple(('RECORD ERROR', name + ': ' + error) for name, error in state.get('record_errors', {}).items())
            rows += tuple((key.upper() + ' P95/P99', f"{value['p95']:.1f}/{value['p99']:.1f}ms")
                          for key, value in self.vision.performance().items())
            return Result('\n'.join(f'{key:<17} {value}' for key,value in rows))
        if len(parts) in (2,3) and lower[1]=='hands':
            if len(parts)==3:
                if lower[2] not in ('on','off'): raise ValueError('Usage: vision hands [on|off]')
                self.vision.set_hands_enabled(lower[2]=='on')
            state=self.vision.snapshot()
            return Result('HAND TRACKING '+('ON' if state['hands_enabled'] else 'OFF')+
                            '\nOPERATOR or above must be recognized. Applies to this run.\n'+
                            'Also controls I Love You and app gestures. CONTROL MODE needs SUPERVISOR or above.')
        if len(parts) in (2,3) and lower[1]=='apps':
            if len(parts)==3:
                if lower[2] not in ('on','off'):raise ValueError('Usage: vision apps [on|off]')
                self.vision.set_apps_enabled(lower[2]=='on')
            return Result('APP CONTROL '+('ON' if self.vision.snapshot()['apps_enabled'] else 'OFF')+
                          '\nADMIN face + one hand. Hold 1 second; lower hand before next action.\n'+
                          self.vision.app_shortcuts)
        if len(parts) in (2,3) and lower[1]=='sound':
            if len(parts)==3 and lower[2] not in ('on','off'):
                raise ValueError(f'Usage: vision {lower[1]} [on|off]')
            if len(parts)==3:self.sounds.set_enabled(lower[2]=='on')
            return Result('QUIET SOUNDS '+('ON' if self.sounds.enabled else 'OFF')+'\nApplies to this run.')
        if len(parts) in (2,3) and lower[1]=='debug':
            if len(parts)==3:
                if lower[2] not in ('on','off'): raise ValueError('Usage: vision debug [on|off]')
                self.vision.set_debug(lower[2]=='on')
            state=self.vision.snapshot()
            people={p.identity:p for p in self.repo.people()}
            rows=[]
            for index,face in enumerate(state['faces'],1):
                identity=face.get('identity','UNKNOWN')
                label=people[identity].name if identity in people else identity
                score=face.get('score')
                blur=face.get('blur');light=face.get('brightness')
                rows.append([index,label,'--' if score is None else f'{score:.3f}',
                             '--' if blur is None else f'{blur:.1f}',
                             '--' if light is None else f'{light:.1f}',face.get('reason','--')])
            header='DEBUG OVERLAY '+('ON' if state['debug'] else 'OFF')
            detail=table(['FACE','RESULT','SIMILARITY','BLUR','LIGHT','REASON'],rows) if rows else 'No faces in the latest frame.'
            return Result(header+'\n'+detail+'\nSimilarity is a cosine score, not a confidence percentage.')
        if len(parts) in (2,3) and lower[1]=='threshold':
            if len(parts)==3:
                if actor.role!='ADMIN': raise ValueError('Only ADMIN may change the match threshold.')
                self.vision.set_threshold(parts[2])
                self.repo.log(actor.name,'VISION_THRESHOLD',detail=str(self.vision.snapshot()['threshold']))
            return Result(f"MATCH THRESHOLD {self.vision.snapshot()['threshold']:.3f}\nApplies to this run; restart uses the launch threshold.")
        raise ValueError('Unknown vision command. Type help.')

    def find(self, name):
        person = self.repo.find(name)
        if person is None: raise ValueError("No profile named " + clean_name(name) + ".")
        return person

    @staticmethod
    def info(person):
        return '\n'.join(f"{key:<14} {value}" for key, value in (
            ("NAME", person.name), ("FIELD", " & ".join(person.fields)), ("ROLE", person.role),
            ("REGISTERED", registered_text(person)), ("LAST LOGIN", display_time(person.last_login)),
            ("FACE SAMPLES", len(person.templates)), ("STATUS", "ACTIVE" if person.active else "DISABLED")))
