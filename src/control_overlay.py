"""Click-through native tool surfaces, dispatched by the existing Tk loop.

Ink reuses one bitmap and paints only new segments. No idle animation/focus steal.
"""
import ctypes as C
from ctypes import wintypes as W
import math
import time
from desktop_control import COMMANDS
from native_notice_window import NativeNoticeWindow, PAINTSTRUCT, rgb
from windows_tray import signature


class Surface(NativeNoticeWindow):
    def __init__(self, kind):
        self.kind=kind;self.state={};self.width=self.height=1;self.active=False
        super().__init__('')
        signature(self.user,'SetWindowTextW',W.BOOL,W.HWND,W.LPCWSTR)
        self.user.SetWindowTextW(self.hwnd,'Vision Control Control '+kind.title())
        signature(self.gdi,'CreatePen',W.HANDLE,C.c_int,C.c_int,W.DWORD)
        signature(self.gdi,'MoveToEx',W.BOOL,W.HDC,C.c_int,C.c_int,W.LPVOID)
        signature(self.gdi,'LineTo',W.BOOL,W.HDC,C.c_int,C.c_int)
        signature(self.gdi,'Ellipse',W.BOOL,W.HDC,C.c_int,C.c_int,C.c_int,C.c_int)
        signature(self.gdi,'Polygon',W.BOOL,W.HDC,C.POINTER(W.POINT),C.c_int)
        signature(self.gdi,'GetStockObject',W.HANDLE,C.c_int)
        signature(self.gdi,'CreateCompatibleDC',W.HDC,W.HDC)
        signature(self.gdi,'CreateCompatibleBitmap',W.HBITMAP,W.HDC,C.c_int,C.c_int)
        signature(self.gdi,'DeleteDC',W.BOOL,W.HDC)
        signature(self.gdi,'BitBlt',W.BOOL,W.HDC,C.c_int,C.c_int,C.c_int,C.c_int,W.HDC,C.c_int,C.c_int,W.DWORD)
        self.fonts.append(self.gdi.CreateFontW(-round(12*self.scale),0,0,0,500,0,0,0,1,0,0,4,0,'Segoe UI'))
        self.last_bounds=self.last_alpha=self.paint_key=None
        self.paint_count=0

    def present(self, bounds, state, opacity=1.):
        bounds=tuple(map(round,bounds))
        resized=self.last_bounds is None or bounds[2:]!=self.last_bounds[2:]
        self.left,self.top,self.width,self.height=bounds
        self.state=state
        alpha=round(opacity*255)
        if alpha!=self.last_alpha:
            self.user.SetLayeredWindowAttributes(self.hwnd,rgb(1,2,3),alpha,1|2)
            self.last_alpha=alpha
        if bounds!=self.last_bounds:
            self.user.SetWindowPos(self.hwnd,W.HWND(-1),*bounds,0x10|(0 if resized else 1))
            self.last_bounds=bounds
        if self.kind=='wheel':key=(state.get('radius'),state.get('hover'),state.get('commands'),state.get('title'),state.get('status'))
        elif self.kind=='pointer':key=(state.get('pinched'),state.get('mode'),state.get('message') if not state.get('commands') else '')
        elif self.kind=='hud':key=tuple(sorted(state.get('metrics',{}).items()))
        else:key=state.get('snap')
        if resized or key!=self.paint_key:
            self.paint_key=key;self.user.InvalidateRect(self.hwnd,None,False)
        if not self.active:self.user.ShowWindow(self.hwnd,4);self.active=True

    def hide(self):
        if self.active:super().hide();self.active=False

    def line(self, hdc, points, color, width=1):
        pen=self.gdi.CreatePen(0,max(1,round(width*self.scale)),rgb(*color))
        previous=self.gdi.SelectObject(hdc,pen)
        try:
            x,y=points[0];self.gdi.MoveToEx(hdc,round(x),round(y),None)
            for x,y in points[1:]:self.gdi.LineTo(hdc,round(x),round(y))
        finally:self.gdi.SelectObject(hdc,previous);self.gdi.DeleteObject(pen)

    def circle(self, hdc, x,y,r,color,width=1):
        points=[(x+math.cos(i*math.tau/96)*r,y+math.sin(i*math.tau/96)*r) for i in range(97)]
        self.line(hdc,points,color,width)

    def polygon(self, hdc, points, color):
        coordinates=(W.POINT*len(points))(*(W.POINT(round(x),round(y)) for x,y in points))
        brush=self.gdi.CreateSolidBrush(rgb(*color))
        oldbrush=self.gdi.SelectObject(hdc,brush)
        oldpen=self.gdi.SelectObject(hdc,self.gdi.GetStockObject(8))  # NULL_PEN
        try:self.gdi.Polygon(hdc,coordinates,len(points))
        finally:
            self.gdi.SelectObject(hdc,oldpen);self.gdi.SelectObject(hdc,oldbrush);self.gdi.DeleteObject(brush)

    def draw_text(self,hdc,text,x,y,width,color=(177,222,248),font=2):
        previous=self.gdi.SelectObject(hdc,self.fonts[font])
        rectangle=W.RECT(round(x-width/2),round(y-10*self.scale),round(x+width/2),round(y+13*self.scale))
        self.gdi.SetTextColor(hdc,rgb(*color))
        self.user.DrawTextW(hdc,text,-1,C.byref(rectangle),1|4|0x20|0x800)
        self.gdi.SelectObject(hdc,previous)

    def paint(self, hwnd):
        paint=PAINTSTRUCT();hdc=self.user.BeginPaint(hwnd,C.byref(paint))
        memory=bitmap=previous=None
        try:
            if not self.fonts or not self.brush:return
            rectangle=W.RECT();self.user.GetClientRect(hwnd,C.byref(rectangle))
            memory=self.gdi.CreateCompatibleDC(hdc)
            bitmap=self.gdi.CreateCompatibleBitmap(hdc,max(1,rectangle.right),max(1,rectangle.bottom))
            if not memory or not bitmap:return
            previous=self.gdi.SelectObject(memory,bitmap)
            # Compose offscreen. Clearing the colour key must not briefly punch
            # a visible hole through the wheel before its lines are repainted.
            self.user.FillRect(memory,C.byref(rectangle),self.brush)
            self.gdi.SetBkMode(memory,1)
            if self.kind=='wheel':self.paint_wheel(memory)
            elif self.kind=='pointer':self.paint_pointer(memory)
            elif self.kind=='hud':self.paint_hud(memory)
            else:self.paint_outline(memory)
            self.gdi.BitBlt(hdc,0,0,rectangle.right,rectangle.bottom,memory,0,0,0x00CC0020)
            self.paint_count+=1
        finally:
            if previous:self.gdi.SelectObject(memory,previous)
            if bitmap:self.gdi.DeleteObject(bitmap)
            if memory:self.gdi.DeleteDC(memory)
            self.user.EndPaint(hwnd,C.byref(paint))

    def paint_wheel(self, hdc):
        if 'radius' not in self.state:return
        r=self.state['radius'];x=y=self.width/2
        hover=self.state.get('hover')
        commands=self.state.get('commands') or COMMANDS;step=math.tau/len(commands)
        for index,(_,label) in enumerate(commands):
            start=-math.pi/2+index*step-step/2+.022
            end=start+step-.044
            points=[]
            for radius,angles in ((r,[start+(end-start)*i/16 for i in range(17)]),
                                  (r*.38,[end-(end-start)*i/16 for i in range(17)])):
                points.extend((x+math.cos(a)*radius,y+math.sin(a)*radius) for a in angles)
            self.polygon(hdc,points,(15,53,76) if hover==index else (8,24,37))
            self.line(hdc,points+[points[0]],(118,219,255) if hover==index else (35,85,112),2 if hover==index else 1)
            a=-math.pi/2+index*step
            tx,ty=x+math.cos(a)*r*.72,y+math.sin(a)*r*.72
            # Two small rows keep labels clear even at 100% desktop scaling.
            words=label.split(' ',1)
            if len(words)==1:self.draw_text(hdc,label,tx,ty,r*.65,(232,248,255) if hover==index else (150,194,217))
            else:
                self.draw_text(hdc,words[0],tx,ty-8*self.scale,r*.65)
                self.draw_text(hdc,words[1],tx,ty+9*self.scale,r*.65)
        self.circle(hdc,x,y,r*1.045,(39,112,149))
        self.circle(hdc,x,y,r*.32,(110,209,254))
        self.draw_text(hdc,self.state.get('title','CONTROL'),x,y-7*self.scale,r*.64,(197,237,255))
        self.draw_text(hdc,'PINCH',x,y+11*self.scale,r*.64,(90,158,191))
        self.draw_text(hdc,self.state.get('status') or 'POINT / PINCH  /  FIST TO BACK  /  ESC TO EXIT',x,y+r*1.16,r*2.4,(145,194,219))

    def paint_pointer(self,hdc):
        x=self.width/2;y=23*self.scale
        pinched=self.state.get('pinched',False)
        self.circle(hdc,x,y,7*self.scale if pinched else 11*self.scale,(180,239,255),2)
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            self.line(hdc,[(x+dx*15*self.scale,y+dy*15*self.scale),
                           (x+dx*20*self.scale,y+dy*20*self.scale)],(77,179,226))
        if not self.state.get('commands') and self.state.get('mode')!='wheel':
            self.draw_text(hdc,self.state.get('message',''),x,55*self.scale,self.width-8,(174,225,250))

    def paint_outline(self,hdc):
        w,h=self.width-2,self.height-2;length=min(30*self.scale,w/3,h/3)
        color=(113,221,255) if self.state.get('snap') else (55,163,215)
        for x,y,dx,dy in ((1,1,1,1),(w,1,-1,1),(1,h,1,-1),(w,h,-1,-1)):
            self.line(hdc,[(x+dx*length,y),(x,y),(x,y+dy*length)],color,2)

    def paint_hud(self,hdc):
        metrics=self.state.get('metrics',{});s=self.scale;w=self.width
        self.draw_text(hdc,'SYSTEM / LIVE',w/2,17*s,w,(164,223,253))
        for index,(key,label) in enumerate((('cpu','CPU'),('gpu','NVIDIA GPU'),('ram','RAM'))):
            value=metrics.get(key);y=(44+34*index)*s
            text='--' if value is None else f'{value:.0f}%'
            self.draw_text(hdc,label+'   '+text,w/2,y,w,(210,239,254))
            self.line(hdc,[(18*s,y+13*s),(w-18*s,y+13*s)],(27,68,91),3)
            if value is not None:
                self.line(hdc,[(18*s,y+13*s),(18*s+(w-36*s)*min(100,max(0,value))/100,y+13*s)],(78,190,245),3)
        if metrics.get('used') is not None:
            self.draw_text(hdc,f"{metrics['used']:.1f} / {metrics['total']:.1f} GB",w/2,151*s,w,(123,177,208))


class InkSurface(Surface):
    """Reusable GDI canvas; append-only frames draw only their new line segments."""
    def __init__(self):
        self.memory=self.bitmap=self.selected=None;self.cached_size=None
        self.ids=();self.counts=();self.ink_key=None
        self.stroke_refs=()
        super().__init__('ink')

    def present(self,bounds,state,opacity=1.):
        old=self.ink_key
        # The base surface handles placement/alpha, not ink invalidation.
        super().present(bounds,state,opacity)
        key=(state.get('ink_revision'),tuple(id(stroke) for stroke in state['ink']))
        if key!=old:
            self.ink_key=key
            # The native paint region clips the bitmap blit. Large rebuilds
            # happen only for undo/clear; ordinary strokes dirty small bounds.
            strokes=state['ink'];ids=key[1]
            incremental=ids[:len(self.ids)]==self.ids and len(ids)>=len(self.ids)
            points=[]
            if incremental:
                for i,stroke in enumerate(strokes):
                    count=self.counts[i] if i<len(self.counts) else 0
                    if len(stroke)<count:incremental=False;break
                    if len(stroke)>count:points.extend(stroke[max(0,count-1):])
            if incremental and points:
                pad=round(5*self.scale)
                xs,ys=zip(*points)
                rectangle=W.RECT(round(min(xs)-self.left-pad),round(min(ys)-self.top-pad),
                                 round(max(xs)-self.left+pad+1),round(max(ys)-self.top+pad+1))
                self.user.InvalidateRect(self.hwnd,C.byref(rectangle),False)
            else:self.user.InvalidateRect(self.hwnd,None,False)

    def free_bitmap(self):
        if self.selected:self.gdi.SelectObject(self.memory,self.selected)
        if self.bitmap:self.gdi.DeleteObject(self.bitmap)
        if self.memory:self.gdi.DeleteDC(self.memory)
        self.memory=self.bitmap=self.selected=None;self.cached_size=None

    def paint(self,hwnd):
        paint=PAINTSTRUCT();hdc=self.user.BeginPaint(hwnd,C.byref(paint))
        try:
            if not self.fonts or not self.brush:return
            size=(max(1,self.width),max(1,self.height))
            rebuilt=size!=self.cached_size
            if rebuilt:
                self.free_bitmap();self.memory=self.gdi.CreateCompatibleDC(hdc)
                self.bitmap=self.gdi.CreateCompatibleBitmap(hdc,*size)
                if not self.memory or not self.bitmap:self.free_bitmap();return
                self.selected=self.gdi.SelectObject(self.memory,self.bitmap);self.cached_size=size
            strokes=self.state.get('ink',[]);ids=tuple(id(stroke) for stroke in strokes)
            incremental=(not rebuilt and ids[:len(self.ids)]==self.ids and len(ids)>=len(self.ids)
                         and all(len(strokes[i])>=count for i,count in enumerate(self.counts)))
            if not incremental:
                rectangle=W.RECT(0,0,*size);self.user.FillRect(self.memory,C.byref(rectangle),self.brush)
            for i,stroke in enumerate(strokes):
                count=self.counts[i] if incremental and i<len(self.counts) else 0
                if len(stroke)<=count:continue
                local=[(x-self.left,y-self.top) for x,y in stroke[max(0,count-1):]]
                if len(local)==1:
                    x,y=local[0];local.append((x+1,y+1))
                self.line(self.memory,local,(97,207,255),3)
            self.ids=ids;self.counts=tuple(map(len,strokes))
            self.stroke_refs=tuple(strokes)  # Prevent recycled Python ids after clear/undo.
            r=paint.rcPaint
            self.gdi.BitBlt(hdc,r.left,r.top,r.right-r.left,r.bottom-r.top,self.memory,r.left,r.top,0x00CC0020)
            self.paint_count+=1
        finally:self.user.EndPaint(hwnd,C.byref(paint))

    def close(self):
        self.free_bitmap();super().close()


class ControlOverlay:
    def __init__(self):
        self.wheel=self.pointer=self.outline=self.hud=self.ink=None
        self.opened=None
        try:
            self.wheel=Surface('wheel');self.pointer=Surface('pointer');self.outline=Surface('outline')
            self.hud=Surface('hud');self.ink=InkSurface()
        except Exception:
            self.close();raise

    def show(self, state, backend):
        area=backend.area;scale=backend.scale
        commands=state.get('commands') or (COMMANDS if state['mode']=='wheel' else None)
        if state.get('hud'):
            self.hud.present((area.right-204*scale,area.top+22*scale,188*scale,174*scale),
                             dict(metrics=state.get('metrics',{})),.94)
        else:self.hud.hide()
        if state.get('ink'):
            self.ink.present((area.left,area.top,area.width,area.height),state,.95)
        else:self.ink.hide()
        if commands:
            if self.opened is None:self.opened=time.monotonic()
            extent=round(backend.radius*2.5)
            x,y=backend.center
            alpha=min(1.,(time.monotonic()-self.opened)/.22)
            self.wheel.present((x-extent/2,y-extent/2,extent,extent),
                               dict(radius=backend.radius,hover=state['hover'],commands=commands,
                                    title=state.get('title','CONTROL'),
                                    status=state.get('message') if state.get('message','').startswith(('INPUT BUSY','MEDIA','VOLUME','DESK')) else ''),alpha)
        else:self.wheel.hide();self.opened=None
        if not state.get('tracking',True) or state.get('point') is None:
            self.pointer.hide();self.outline.hide();return
        x,y=state['point'];width=round(390*scale);height=round(76*scale)
        # The marker is exactly at the hand point, even near screen edges.
        self.pointer.present((x-width/2,y-23*scale,width,height),state)
        rectangle=state.get('region') or state['preview'] or state['target']
        if rectangle is None:self.outline.hide()
        else:self.outline.present((rectangle.left,rectangle.top,rectangle.width,rectangle.height),
                                  dict(snap=state['preview'] is not None),.9)

    def hide(self):
        for surface in (self.wheel,self.pointer,self.outline,self.hud,self.ink):
            if surface:surface.hide()
        self.opened=None

    def close(self):
        for surface in (self.wheel,self.pointer,self.outline,self.hud,self.ink):
            if surface:surface.close()
        self.wheel=self.pointer=self.outline=self.hud=self.ink=None
