"""Refresh an existing profile's face samples in the current camera window."""
import importlib
import sqlite3
import threading
import time
import tkinter as tk
import cv2
import numpy as np
from person_store import role_for_code, ACCESS_FILE
from camera_view import fitted_size

enrollment=importlib.import_module('face_enrollment')


class RecaptureSession:
    def __init__(self, repo, plan, actor_identity, clock=time.monotonic):
        self.repo,self.plan,self.actor_identity=repo,plan,actor_identity
        self.target=repo.validate_recapture(plan,actor_identity)
        self.total=len(self.target.templates)
        self.clock,self.deadline=clock,clock()+300
        self.samples=[]
        self.lock=threading.RLock()
        self.cancelled=self.saved=False
        self.backup=None

    def snapshot(self):
        with self.lock:
            return len(self.samples),self.total,self.saved,self.cancelled

    def cancel(self):
        with self.lock:
            self.cancelled=True
            self.samples.clear()

    def capture(self, frame, faces, recognizer):
        with self.lock:
            if self.cancelled or self.saved: return 'Recapture is closed.'
            if self.clock()>=self.deadline: raise ValueError('Recapture expired. Cancel and run the command again.')
            if len(self.samples)>=self.total: return 'Samples ready. Return the manager to the camera and press SAVE.'
            target=self.repo.validate_recapture(self.plan,self.actor_identity)
            face,reason=enrollment.select_face(faces,frame.shape)
            if face is None:
                return 'Only the target person may be visible; keep their whole face close to the camera.'
            aligned=recognizer.alignCrop(frame,face)
            gray=cv2.cvtColor(aligned,cv2.COLOR_BGR2GRAY)
            if cv2.Laplacian(gray,cv2.CV_64F).var()<25 or not 35<=gray.mean()<=220:
                return 'Hold still and adjust the lighting. No sample captured.'
            feature=enrollment.normalize(recognizer.feature(aligned))
            own=float(np.max(target.templates@feature))
            if own<0.65: return 'This face does not match the target profile. No sample captured.'
            for person in self.repo.people():
                if person.identity!=target.identity:
                    score=float(np.max(person.templates@feature))
                    if score>=0.65 or own-score<0.05:
                        return 'This face matches another profile or is ambiguous. No sample captured.'
            if self.samples and np.dot(self.samples[0],feature)<enrollment.CONSISTENCY_THRESHOLD:
                return 'Face changed between samples. Try again.'
            self.samples.append(feature.copy())
            if len(self.samples)==self.total:
                return 'Samples ready. Return the original manager to the camera and press SAVE.'
            return 'Sample captured. Follow the next pose.'

    def commit(self, gate, code='', access_file=ACCESS_FILE):
        with self.lock:
            if self.cancelled or self.saved: raise ValueError('Recapture is closed.')
            if self.clock()>=self.deadline: raise ValueError('Recapture expired. The old record was preserved.')
            if len(self.samples)!=self.total: raise ValueError('Complete every sample before saving.')
            self.repo.validate_recapture(self.plan,self.actor_identity)
            actor=self.repo.get(self.actor_identity)
            if not gate.valid(self.actor_identity):
                candidate=gate.candidate
                if not candidate or candidate.identity!=self.actor_identity or gate.clock()-gate.candidate_at>1.5:
                    raise ValueError('The original manager must be recognized before SAVE.')
                if actor.role=='SUPERVISOR':
                    try: correct=role_for_code(code,access_file)=='SUPERVISOR'
                    except ValueError: correct=False
                    if not correct: raise ValueError('Session locked: enter the SUPERVISOR code to save.')
                gate.open()
            self.backup=self.repo.replace_templates(self.plan,self.actor_identity,self.samples)
            self.saved=True
            self.samples.clear()
            return f'Face samples refreshed for {actor.name if self.target.identity==actor.identity else self.target.name}. Previous samples backed up.'


class RecaptureView:
    def __init__(self, parent, session, gate, request_capture, on_done):
        self.session,self.gate,self.request_capture,self.on_done=session,gate,request_capture,on_done
        self.closed=self.busy=False
        self.photo=self.last_frame=None
        self.window=tk.Frame(parent,bg='#0d1823',highlightthickness=1,highlightbackground='#49677e')
        self.window.place(relx=.03,rely=.04,relwidth=.94,relheight=.92)
        self.window.lift()
        top=tk.Frame(self.window,bg='#1c2d3d');top.pack(fill='x')
        tk.Label(top,text='FACE RECAPTURE',bg='#1c2d3d',fg='#d4e0e8',font=('Consolas',16,'bold'),padx=16,pady=10).pack(side='left')
        tk.Button(top,text='CANCEL / BACK',command=self.cancel,bg='#24394c',fg='#76c4ee',relief='flat',padx=12,pady=8).pack(side='right',padx=12)
        target=session.target
        tk.Label(self.window,text=f'{target.name}  /  {target.role}  /  '+ ' & '.join(target.fields),bg='#0d1823',fg='#76c4ee',font=('Consolas',11),anchor='w').pack(fill='x',padx=18,pady=(12,6))
        self.pose=tk.Label(self.window,bg='#0d1823',fg='#d4e0e8',font=('Consolas',11),anchor='w')
        self.pose.pack(fill='x',padx=18)
        self.status=tk.Label(self.window,text='Only the target person should be visible. CAPTURE / K for each pose.',bg='#0d1823',fg='#8fa7ba',font=('Consolas',10),anchor='w',wraplength=540,justify='left')
        self.controls=tk.Frame(self.window,bg='#0d1823')
        self.controls.pack(side='bottom',fill='x',padx=18,pady=(6,14))
        self.capture_button=tk.Button(self.controls,text='CAPTURE [K]',command=self.capture,bg='#294d68',fg='#d4e0e8',relief='raised',padx=12,pady=7,state='disabled')
        self.capture_button.pack(side='left')
        self.save_button=tk.Button(self.controls,text='SAVE',command=self.save,bg='#294d68',fg='#d4e0e8',relief='raised',padx=18,pady=7,state='disabled')
        self.save_button.pack(side='right')
        self.code_row=tk.Frame(self.window,bg='#0d1823')
        actor=session.repo.get(session.actor_identity)
        if actor.role=='SUPERVISOR':
            tk.Label(self.code_row,text='SUPERVISOR CODE (if session locked):',bg='#0d1823',fg='#8fa7ba',font=('Consolas',10)).pack(side='left')
            self.code=tk.Entry(self.code_row,show='*',bg='#101e2b',fg='#d4e0e8',insertbackground='#76c4ee',relief='flat')
            self.code.pack(side='left',fill='x',expand=True,padx=(8,0))
            self.code_row.pack(side='bottom',fill='x',padx=18,pady=4)
        else: self.code=None
        self.status.pack(side='bottom',fill='x',padx=18,pady=6)
        self.canvas=tk.Canvas(self.window,bg='#101e2b',width=1,height=1,highlightthickness=0)
        self.canvas.pack(fill='both',expand=True,padx=18,pady=(8,4))
        self.image_item=self.canvas.create_image(0,0,anchor='center')
        self.canvas.bind('<Configure>',lambda e:self.redraw())
        self.refresh_pose()

    def refresh_pose(self):
        count,total,saved,cancelled=self.session.snapshot()
        if count>=total:
            self.pose.configure(text=f'{total} / {total}  -  READY TO SAVE')
            self.save_button.configure(state='normal')
            self.capture_button.configure(state='disabled')
        else:
            poses=('FRONT','SLIGHTLY RIGHT','SLIGHTLY LEFT',
                   'HEADSET: FRONT','HEADSET: SLIGHTLY RIGHT','HEADSET: SLIGHTLY LEFT')
            self.pose.configure(text=f'{count} / {total}  -  NEXT: {poses[count]}')
            self.capture_button.configure(state='normal' if self.last_frame is not None and not self.busy else 'disabled')

    def show_frame(self, frame):
        if self.closed:return
        self.last_frame=frame
        self.redraw();self.refresh_pose()

    def redraw(self):
        if self.closed or self.last_frame is None:return
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height()
        if w<2 or h<2:return
        vw,vh=fitted_size(self.last_frame.shape,w,h)
        image=cv2.resize(self.last_frame,(vw,vh))
        rgb=cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
        self.photo=tk.PhotoImage(master=self.window,data=f'P6\n{vw} {vh}\n255\n'.encode()+rgb.tobytes(),format='PPM')
        self.canvas.itemconfigure(self.image_item,image=self.photo)
        self.canvas.coords(self.image_item,w/2,h/2)

    def capture(self):
        count,total,_,_=self.session.snapshot()
        if self.closed or self.busy or self.last_frame is None or count>=total:return
        self.busy=True
        self.capture_button.configure(state='disabled')
        try:self.request_capture(self.session)
        except (ValueError,RuntimeError) as error:
            self.busy=False;self.status.configure(text=str(error));self.refresh_pose()

    def captured(self, message):
        if self.closed:return
        self.busy=False
        self.status.configure(text=message)
        self.refresh_pose()

    def save(self):
        if self.closed:return
        code=self.code.get() if self.code is not None else ''
        if self.code is not None:self.code.delete(0,'end')
        try:
            message=self.session.commit(self.gate,code)
        except (ValueError,RuntimeError,OSError,sqlite3.Error) as error:
            self.status.configure(text=str(error));return
        self.close(True,message)

    def cancel(self):
        if not self.closed:self.close(False,'Recapture cancelled. Original samples preserved.')

    def close(self, saved, message):
        if self.closed:return
        self.closed=True
        self.session.cancel()
        if self.code is not None:self.code.delete(0,'end')
        self.last_frame=self.photo=None
        self.window.destroy()
        self.on_done(saved,message)
