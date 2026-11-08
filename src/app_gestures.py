"""One deliberate action, then release; recognition never grants management rights."""

APP_NAMES = dict.fromkeys(('1', '2', '3', '4', '5', 'fist'), 'Unassigned')
ACTION_NAMES={**APP_NAMES,'session':'SESSION START'}


class AppGesture:
    def __init__(self,hold=1.,release=.4,cooldown=1.2):
        self.hold,self.release,self.cooldown=hold,release,cooldown
        self.last=self.since=self.key=self.neutral=None
        self.armed=False
        self.fired=float('-inf')

    def suppress(self):
        self.armed=False
        self.since=self.key=self.neutral=None

    @staticmethod
    def action(hands):
        if len(hands)!=1:return None
        hand=hands[0]
        if hand.get('gesture') in ('ILoveYou','Thumb_Up','Thumb_Down'):return None
        if hand.get('session_start') is True:return 'session'
        count=hand.get('fingers')
        if type(count) is int and 1<=count<=5:return str(count)
        if type(count) is int and count==0 and hand.get('gesture')=='Closed_Fist':return 'fist'
        return None

    def update(self,hands,identity,now,enabled=True):
        continuous=self.last is not None and 0<=now-self.last<=.35
        self.last=now
        if not enabled or identity is None or len(hands)>1:
            self.suppress();return None
        action=self.action(hands)
        if action is None:
            self.key=self.since=None
            if self.neutral is None or not continuous:self.neutral=now
            if now-self.neutral>=self.release:self.armed=True
            return None
        self.neutral=None
        if not self.armed:return None
        key=(identity,hands[0]['number'],action)
        if not continuous or self.key!=key:self.key,self.since=key,now
        if now-self.since<self.hold or now-self.fired<self.cooldown:return None
        self.fired=now;self.suppress()
        return action
