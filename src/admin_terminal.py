"""Blue terminal panel over the camera preview."""
import sqlite3
import tkinter as tk
from tkinter import font
from person_store import FIELDS, ROLES
from admin_commands import CommandEngine

BG = '#0d141e'
CHROME = '#1b2c3e'
BLUE = '#6abcf0'
TEXT = '#d1dbe6'
MUTED = '#90a3b9'


class AdminTerminal:
    def __init__(self, parent, repo, gate, actor_identity, on_change=lambda: None, on_close=lambda: None, threshold=0.65, vision=None, presence=None, on_recapture=None, feed=None, sounds=None, on_terminate=None):
        self.repo, self.gate = repo, gate
        self.on_change, self.on_close = on_change, on_close
        self.actor_identity = actor_identity
        self.on_recapture = on_recapture
        self.on_terminate = on_terminate
        self.terminating = False
        self.engine = CommandEngine(repo, actor_identity, gate.valid, threshold=threshold, vision=vision, presence=presence, feed=feed, sounds=sounds)
        self.feed = self.engine.feed
        self.watching = False
        self.watch_cursor = None
        self.history, self.history_index = [], 0
        self.draft, self.completion = '', None
        self.locked = False
        self.closed = False
        self.window = tk.Frame(parent, bg=BG, highlightbackground='#31516e', highlightthickness=1)
        self.window.place(relx=0.03, rely=0.04, relwidth=0.94, relheight=0.92)
        self.window.lift()
        self.mono = font.Font(self.window, family='Consolas', size=11)
        top = tk.Frame(self.window, bg=CHROME)
        top.pack(fill='x')
        self.back_button = tk.Button(top, text='BACK TO CAMERA  ×', command=self.close,
                                     bg=CHROME, fg=BLUE, activebackground='#28455f', activeforeground='white',
                                     font=('Consolas', 10), relief='flat', cursor='hand2', padx=10, pady=5)
        self.back_button.pack(side='right', padx=10)
        self.caption = tk.Label(top, text='', bg=CHROME, fg=TEXT, font=('Consolas', 11), anchor='w', padx=16, pady=10)
        self.caption.pack(side='left', fill='x', expand=True)
        menu_row = tk.Frame(self.window, bg='#142233')
        menu_row.pack(fill='x')

        def add_menu(label, popup):
            button = tk.Menubutton(menu_row, text=label, menu=popup, bg='#142233', fg=TEXT,
                                   activebackground=CHROME, activeforeground=BLUE, font=('Consolas', 10),
                                   relief='flat', padx=10, pady=5)
            button.pack(side='left')
        file_menu = tk.Menu(self.window, tearoff=False)
        file_menu.add_command(label='Lock session', command=self.manual_lock)
        file_menu.add_command(label='Close terminal', command=self.close)
        add_menu('File', file_menu)
        edit_menu = tk.Menu(self.window, tearoff=False)
        edit_menu.add_command(label='Copy', command=self.copy)
        edit_menu.add_command(label='Paste', command=lambda: self.entry.event_generate('<<Paste>>'))
        edit_menu.add_command(label='Select all output', command=self.select_all)
        add_menu('Edit', edit_menu)
        view_menu = tk.Menu(self.window, tearoff=False)
        view_menu.add_command(label='Increase font', command=lambda: self.resize_font(1))
        view_menu.add_command(label='Decrease font', command=lambda: self.resize_font(-1))
        add_menu('View', view_menu)
        search_menu = tk.Menu(self.window, tearoff=False)
        search_menu.add_command(label='Find selected text', command=self.find_selected)
        add_menu('Search', search_menu)
        terminal_menu = tk.Menu(self.window, tearoff=False)
        terminal_menu.add_command(label='Clear output', command=self.clear)
        add_menu('Terminal', terminal_menu)
        help_menu = tk.Menu(self.window, tearoff=False)
        help_menu.add_command(label='Commands', command=lambda: self.execute('help'))
        add_menu('Help', help_menu)
        body = tk.Frame(self.window, bg=BG)
        body.pack(fill='both', expand=True, padx=16, pady=(12, 0))
        self.output = tk.Text(body, bg=BG, fg=TEXT, insertbackground=BLUE, font=self.mono,
                              relief='flat', borderwidth=0, wrap='none', state='disabled',
                              selectbackground='#294665', padx=0, pady=0)
        vertical = tk.Scrollbar(body, command=self.output.yview)
        horizontal = tk.Scrollbar(body, orient='horizontal', command=self.output.xview)
        self.output.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side='right', fill='y')
        horizontal.pack(side='bottom', fill='x')
        self.output.pack(fill='both', expand=True)
        self.output.tag_configure('blue', foreground=BLUE)
        self.output.tag_configure('dim', foreground=MUTED)
        self.output.tag_configure('error', foreground='#f09a9d')
        self.output.tag_configure('success', foreground='#9dceae')
        self.output.tag_configure('search', background='#294665')
        self.output.bind('<Control-c>', lambda e: self.cancel() if self.watching else self.copy())
        self.output.bind('<Control-a>', lambda e: self.select_all())
        input_row = tk.Frame(self.window, bg=BG)
        input_row.pack(fill='x', padx=16, pady=(12, 14))
        self.prompt = tk.Label(input_row, text='', bg=BG, fg=BLUE, font=self.mono)
        self.prompt.pack(side='left')
        self.entry = tk.Entry(input_row, bg=BG, fg=TEXT, insertbackground=BLUE, insertwidth=8,
                              insertontime=600, insertofftime=400, font=self.mono, relief='flat',
                              selectbackground='#294665', disabledbackground=BG, disabledforeground=MUTED)
        self.entry.pack(side='left', fill='x', expand=True, padx=(8, 0))
        self.entry.bind('<Return>', self.submit)
        self.entry.bind('<Up>', lambda e: self.recall(-1))
        self.entry.bind('<Down>', lambda e: self.recall(1))
        self.entry.bind('<Tab>', self.complete)
        self.entry.bind('<Control-c>', self.cancel)
        self.entry.bind('<KeyPress>', self.reset_completion, add='+')
        self.footer = tk.Label(self.window, text='', bg='#142233', fg=MUTED, font=('Consolas', 9),
                               anchor='w', padx=16, pady=6)
        # Reserve space for the command input at small window sizes.
        body.pack_forget()
        input_row.pack_forget()
        self.footer.pack(side='bottom', fill='x')
        input_row.pack(side='bottom', fill='x', padx=16, pady=(12, 14))
        body.pack(fill='both', expand=True, padx=16, pady=(12, 0))
        self.refresh_identity()
        self.write('VISION CONSOLE\n', 'blue')
        actor = self.repo.get(actor_identity)
        self.write(f'Authenticated as {actor.name} · {actor.role}\nType \'help\' to view available commands.\n\n', 'dim')
        self.execute('user base')
        self.entry.focus_set()
        self.timer = self.window.after(100, self.check_session)

    def refresh_identity(self):
        actor = self.repo.get(self.actor_identity)
        if not actor: return
        self.normal_prompt = f'{actor.name.lower()}@vision:~$'
        self.caption.configure(text=f'>_  {actor.name.lower()}@vision: ~')
        self.prompt.configure(text=self.normal_prompt)
        self.footer.configure(text=f'{actor.role} SESSION   ·   AUTO LOCK {self.gate.timeout:g}s   ·   UP/DOWN HISTORY   ·   TAB COMPLETE')

    def write(self, text, tag=None):
        self.output.configure(state='normal')
        self.output.insert('end', text, tag or ())
        if self.watching:
            lines=int(self.output.index('end-1c').split('.')[0])
            if lines>2000:self.output.delete('1.0',f'{lines-2000}.0')
        self.output.configure(state='disabled')
        self.output.see('end')

    def clear(self):
        self.output.configure(state='normal')
        self.output.delete('1.0', 'end')
        self.output.configure(state='disabled')

    def submit(self, event=None):
        if self.watching:return 'break'
        value = self.entry.get()
        secret = self.engine.pending_request == 'password'
        pending = bool(self.engine.pending_request)
        self.entry.delete(0, 'end')
        if self.locked: return 'break'
        if pending:
            self.write(self.prompt.cget('text') + (' [hidden]' if secret else ' ' + value) + '\n', 'dim')
            try: self.handle(self.engine.reply(value))
            except (ValueError, OSError, sqlite3.Error) as error: self.error(error)
        elif value.strip():
            if not self.history or self.history[-1] != value: self.history.append(value)
            self.history_index = len(self.history)
            self.draft = ''
            self.execute(value)
        return 'break'

    def execute(self, command):
        if self.locked or self.terminating: return
        if self.watching:return
        if self.engine.pending_request:
            self.entry.delete(0, "end")
        output_start = self.output.index("end-1c")
        self.write(self.normal_prompt + ' ' + command + '\n', 'blue')
        try: self.handle(self.engine.begin(command))
        except (ValueError, OSError, sqlite3.Error) as error: self.error(error)
        if command.strip().lower() == "help" and not self.locked:
            self.output.yview(output_start)

    def handle(self, result):
        self.entry.configure(show='')
        self.prompt.configure(text=self.normal_prompt)
        if result.text: self.write(result.text + '\n\n', 'success' if result.changed else None)
        if result.changed:
            self.on_change()
            self.refresh_identity()
            actor=self.repo.get(self.actor_identity)
            self.feed.add(f'Profiles updated · {actor.name if actor else "manager"}')
        if result.request:
            if result.request == 'password':
                actor = self.repo.get(self.actor_identity)
                self.prompt.configure(text=f'[sudo] password for {actor.name}:')
                self.entry.configure(show='*')
            else:
                self.prompt.configure(text=f'Remove {result.plan.target_name}? [y/N]:')
        if result.action == 'clear': self.clear()
        elif result.action == 'lock': self.manual_lock()
        elif result.action == 'exit': self.close()
        elif result.action == 'terminate':
            if self.on_terminate is None:
                self.write('Termination is unavailable in this view.\n','error')
            else:self.on_terminate()
        elif result.action == 'watch': self.start_watch()
        elif result.action == 'recapture':
            if self.on_recapture is None:
                self.write('Recapture is unavailable in this view.\n','error')
            else:
                self.on_recapture(result.plan)

    def error(self, error):
        self.engine.cancel()
        self.entry.configure(show='')
        self.prompt.configure(text=self.normal_prompt)
        self.write('error: ' + str(error) + '\n\n', 'error')
        if not self.gate.valid(self.actor_identity): self.set_locked()

    def cancel(self, event=None):
        self.stop_watch()
        self.engine.cancel()
        self.entry.delete(0, 'end')
        self.entry.configure(show='')
        self.prompt.configure(text=self.normal_prompt)
        self.write('^C\n', 'dim')
        return 'break'

    def recall(self, step):
        if self.locked or self.watching or self.engine.pending_request: return 'break'
        if self.history_index == len(self.history): self.draft = self.entry.get()
        self.history_index = max(0, min(len(self.history), self.history_index + step))
        text = self.draft if self.history_index == len(self.history) else self.history[self.history_index]
        self.entry.delete(0, 'end'); self.entry.insert(0, text)
        return 'break'

    def reset_completion(self, event):
        if event.keysym != 'Tab': self.completion = None

    def complete(self, event=None):
        if self.locked or self.watching or self.engine.pending_request: return 'break'
        text = self.entry.get()
        if self.completion and text == self.completion[0]:
            _, prefix, choices, index = self.completion
            index = (index + 1) % len(choices)
        else:
            prefix, _, partial = text.rpartition(' ')
            words = prefix.split()
            if words and words[0] == 'sudo': words = words[1:]
            if not words: options = ['user', 'vision', 'status', 'whoami', 'logs', 'watch', 'help', 'clear', 'lock', 'exit', 'terminate', 'sudo']
            elif words == ['terminate']: options = ['connection']
            elif words == ['vision']: options = ['status', 'debug', 'threshold', 'sound', 'hands','apps']
            elif words in (['vision','debug'],['vision','sound'],['vision','hands'],['vision','apps']): options = ['on', 'off']
            elif words == ['user']: options = ['base', 'edit'] + [p.name for p in self.repo.people()]
            elif words == ['user', 'edit']: options = [p.name for p in self.repo.people()]
            elif len(words) == 3 and words[:2] == ['user', 'edit']: options = ['name', 'field', 'role']
            elif len(words) == 4 and words[:2] == ['user', 'edit']:
                options = list(FIELDS if words[-1] == 'field' else ROLES if words[-1] == 'role' else ())
            elif len(words) == 2 and words[0] == 'user': options = ['info', 'stats', 'recapture', 'enable', 'disable', 'remove']
            else: options = []
            choices = [x for x in options if x.casefold().startswith(partial.casefold())]
            if not choices: return 'break'
            prefix = prefix + ' ' if prefix else ''
            index = 0
        updated = prefix + choices[index]
        self.entry.delete(0, 'end'); self.entry.insert(0, updated)
        self.completion = updated, prefix, choices, index
        return 'break'

    def copy(self):
        try: value = self.output.get('sel.first', 'sel.last')
        except tk.TclError: return 'break'
        self.window.clipboard_clear(); self.window.clipboard_append(value)
        return 'break'

    def select_all(self):
        self.output.tag_add('sel', '1.0', 'end-1c')
        return 'break'

    def find_selected(self):
        try: value = self.output.get('sel.first', 'sel.last')
        except tk.TclError: value = self.entry.get().strip()
        self.output.tag_remove('search', '1.0', 'end')
        if not value: return
        start = '1.0'
        while True:
            found = self.output.search(value, start, stopindex='end', nocase=True)
            if not found: break
            end = f'{found}+{len(value)}c'
            self.output.tag_add('search', found, end)
            start = end

    def resize_font(self, delta):
        self.mono.configure(size=max(8, min(20, self.mono.cget('size') + delta)))

    def manual_lock(self):
        self.gate.lock()
        self.set_locked()

    def set_locked(self):
        if self.locked or self.terminating: return
        self.stop_watch()
        self.locked = True
        self.engine.cancel()
        self.entry.delete(0, 'end'); self.entry.configure(show='', state='disabled')
        self.clear()
        self.write('SESSION LOCKED\n\n', 'blue')
        self.write(self.gate.reason + '\n', 'dim')
        self.prompt.configure(text='locked')
        self.footer.configure(text='LOCKED   ·   RETURN TO THE CAMERA TO UNLOCK')

    def check_session(self):
        if self.closed or self.terminating: return
        if hasattr(self, "timer"):
            self.window.after_cancel(self.timer)
        try:
            self.engine.actor()
            if self.watching and not self.locked:self.update_watch()
        except (ValueError,OSError,sqlite3.Error):self.set_locked()
        self.timer = self.window.after(100, self.check_session)

    def start_watch(self):
        self.engine.actor()
        self.watching=True
        self.entry.delete(0,'end')
        self.entry.configure(state='readonly')
        self.prompt.configure(text='watch · Ctrl+C to stop')
        self.write('LIVE EVENTS · UTC+03:00 · Ctrl+C to return\n\n','dim')
        self.watch_cursor=None
        self.update_watch()
        self.entry.focus_set()

    def update_watch(self):
        # Validate the current manager before consuming or showing any new events.
        self.engine.actor()
        lines,self.watch_cursor=self.feed.read(self.watch_cursor)
        if lines:self.write('\n'.join(lines)+'\n')

    def stop_watch(self):
        if not self.watching:return
        self.watching=False
        self.watch_cursor=None
        if not self.closed and not self.locked:
            self.entry.configure(state='normal')
            self.prompt.configure(text=self.normal_prompt)

    def begin_shutdown(self):
        self.stop_watch()
        self.engine.cancel()
        self.terminating = True
        if hasattr(self,'timer'):self.window.after_cancel(self.timer)
        self.entry.delete(0,'end')
        self.entry.configure(show='',state='disabled')
        self.back_button.configure(state='disabled')
        self.prompt.configure(text='offline')
        self.footer.configure(text='TERMINATING CONNECTION')
        self.write('\nReleasing camera...\n','dim')

    def close(self):
        self.stop_watch()
        self.engine.cancel()
        self.gate.lock()
        if self.closed: return
        self.closed = True
        if hasattr(self, 'timer'): self.window.after_cancel(self.timer)
        self.entry.delete(0, 'end')
        self.window.destroy()
        self.on_close()
