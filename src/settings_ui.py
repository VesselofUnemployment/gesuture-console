"""Camera-free Windows settings window for enrollment codes and gestures."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tkinter as tk
from tkinter import filedialog, ttk

from app_gestures import APP_NAMES
from app_launcher import GESTURE_CONFIG, SESSION_CONFIG, SESSION_KEYS, load_targets
from person_store import ACCESS_FILE
from settings_store import PASSWORD_ROLES, save_access_codes, save_shortcuts


def installed_apps():
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run(
        [str(powershell), '-NoProfile', '-NonInteractive', '-Command',
         '[Console]::OutputEncoding = [Text.Encoding]::UTF8; '
         'Get-StartApps -ErrorAction Stop | Select-Object Name, AppID | ConvertTo-Json -Compress'],
        capture_output=True, check=True, encoding='utf-8', timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    rows = json.loads(result.stdout or '[]')
    if isinstance(rows, dict):
        rows = [rows]
    apps = sorted({(row['Name'], row['AppID']) for row in rows
                   if re.fullmatch(r'[A-Za-z0-9_.{}-]+![A-Za-z0-9_.{}-]+', row['AppID'])})
    counts = Counter(name for name, _ in apps)
    return [(name if counts[name] == 1 else f'{name} ({app_id})', app_id) for name, app_id in apps]


class SettingsWindow:
    def __init__(self, root, directory=ACCESS_FILE.parent):
        self.root = root
        self.access_path = directory / ACCESS_FILE.name
        self.current = tk.StringVar(root)
        self.status = tk.StringVar(root)
        self.passwords = {}
        self.apps = None
        root.title('Settings')
        root.minsize(660, 440)
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill='both', expand=True)
        self.instructions = ttk.Label(outer, wraplength=620)
        self.instructions.pack(anchor='w')
        auth = ttk.Frame(outer)
        auth.pack(fill='x', pady=(8, 12))
        ttk.Label(auth, text='Current ADMIN password').pack(side='left')
        self.admin_entry = ttk.Entry(auth, textvariable=self.current, show='*', width=30)
        self.admin_entry.pack(side='left', padx=10)

        tabs = ttk.Notebook(outer)
        tabs.pack(fill='both', expand=True)
        passwords = ttk.Frame(tabs, padding=12)
        shortcuts = ttk.Frame(tabs, padding=12)
        tabs.add(passwords, text='Role Passwords')
        tabs.add(shortcuts, text='App Shortcuts')
        self.password_tab(passwords)
        session = ttk.Frame(tabs, padding=12)
        tabs.add(session, text='Session Start')
        self.shortcut_tab(shortcuts, directory / GESTURE_CONFIG.name, APP_NAMES,
                          'Choose an app for each gesture you want to use.')
        self.shortcut_tab(session, directory / SESSION_CONFIG.name, SESSION_KEYS,
                          'Choose up to five apps to open together, in slot order.')
        ttk.Label(outer, textvariable=self.status, wraplength=620).pack(anchor='w', pady=(10, 0))
        self.refresh_instructions()

    def refresh_instructions(self):
        configured = self.access_path.exists()
        self.admin_entry.configure(state='normal' if configured else 'disabled')
        self.instructions.configure(text=(
            'Enter your current ADMIN password to save changes.' if configured else
            'Set the three role passwords, then use register.cmd to enroll your face as ADMIN.'
        ))

    def password_tab(self, frame):
        ttk.Label(frame, text='Choose a different password of 10–128 characters for each role.\n'
                             'Leave both fields empty to keep the current password. USER needs no password.'
                  ).grid(row=0, columnspan=3, sticky='w', pady=(0, 12))
        ttk.Label(frame, text='New password').grid(row=1, column=1, sticky='w')
        ttk.Label(frame, text='Confirm password').grid(row=1, column=2, sticky='w')
        for row, role in enumerate(PASSWORD_ROLES, start=2):
            first, second = tk.StringVar(self.root), tk.StringVar(self.root)
            self.passwords[role] = first, second
            ttk.Label(frame, text=role).grid(row=row, column=0, sticky='w', padx=(0, 14))
            ttk.Entry(frame, textvariable=first, show='*', width=23).grid(row=row, column=1, padx=(0, 10), pady=4)
            ttk.Entry(frame, textvariable=second, show='*', width=23).grid(row=row, column=2, pady=4)
        ttk.Button(frame, text='Save passwords', command=self.save_passwords).grid(
            row=5, columnspan=3, sticky='w', pady=(12, 0))

    def shortcut_tab(self, frame, path, keys, instructions):
        targets = {}
        previous = b''
        try:
            previous = path.read_bytes()
            targets = load_targets(path, keys=keys)
        except FileNotFoundError:
            pass
        except ValueError:
            self.status.set('Could not read the shortcuts. You can choose new ones.')
        except OSError:
            previous = None
            self.status.set('Check file access and reopen Settings.')

        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=instructions).grid(row=0, columnspan=5, sticky='w', pady=(0, 10))
        fields = {}
        for row, key in enumerate(keys, start=1):
            variable = tk.StringVar(self.root, targets.get(key, {}).get('target', ''))
            fields[key] = variable
            ttk.Label(frame, text='Fist' if key == 'fist' else key).grid(row=row, column=0, sticky='w', padx=(0, 12))
            ttk.Entry(frame, textvariable=variable, state='readonly').grid(row=row, column=1, sticky='ew', pady=3)
            ttk.Button(frame, text='Browse', command=lambda v=variable: self.pick_file(v)).grid(row=row, column=2, padx=6)
            ttk.Button(frame, text='Store app', command=lambda v=variable: self.pick_app(v)).grid(row=row, column=3)
            ttk.Button(frame, text='Clear', command=lambda v=variable: v.set('')).grid(row=row, column=4, padx=(6, 0))

        def save():
            nonlocal previous
            previous = self.save_apps(path, keys, fields, previous)

        ttk.Button(frame, text='Save shortcuts', command=save,
                   state='disabled' if previous is None else 'normal').grid(
            row=len(keys) + 1, columnspan=5, sticky='w', pady=(12, 0))

    def pick_file(self, variable):
        path = filedialog.askopenfilename(parent=self.root, title='Choose an app or shortcut',
                                          filetypes=[('Apps and shortcuts', ('*.exe', '*.lnk'))])
        if path:
            variable.set(str(Path(path)))

    def pick_app(self, variable):
        if self.apps is None:
            self.root.configure(cursor='watch')
            self.root.update_idletasks()
            try:
                self.apps = installed_apps()
            except (OSError, subprocess.SubprocessError, ValueError):
                self.status.set('Could not list Store apps. Use Browse to select a shortcut.')
                return
            finally:
                self.root.configure(cursor='')
        if not self.apps:
            self.status.set('No selectable Store apps were found in the Start menu.')
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('Choose a Store app')
        dialog.transient(self.root)
        dialog.grab_set()
        choices = ttk.Combobox(dialog, state='readonly', width=48,
                               values=[name for name, _ in self.apps])
        choices.pack(padx=14, pady=14)
        choices.current(0)

        def select():
            variable.set(self.apps[choices.current()][1])
            dialog.destroy()

        ttk.Button(dialog, text='Select', command=select).pack(pady=(0, 14))
        choices.bind('<Return>', lambda event: select())
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        choices.focus_set()

    def save_passwords(self):
        try:
            changes = {}
            for role, (first, second) in self.passwords.items():
                if first.get() != second.get():
                    raise ValueError(f'The two {role} passwords must match.')
                if first.get():
                    changes[role] = first.get()
            first_setup = not self.access_path.exists()
            save_access_codes(self.current.get(), changes, self.access_path)
        except ValueError as error:
            self.status.set(str(error))
        except (OSError, sqlite3.Error):
            self.status.set('Could not read or save the role passwords.')
        else:
            self.refresh_instructions()
            for first, second in self.passwords.values():
                first.set('')
                second.set('')
            self.status.set('Passwords saved. Open register.cmd to enroll the first admin.'
                            if first_setup else 'Passwords saved. They apply to future registrations and password checks.')
        finally:
            self.current.set('')

    def save_apps(self, path, keys, fields, previous):
        try:
            targets = {}
            for key, variable in fields.items():
                value = variable.get().strip()
                if value:
                    kind = 'file' if Path(value).is_absolute() else 'app'
                    targets[key] = dict(kind=kind, target=value)
            previous = save_shortcuts(self.current.get(), targets, path, self.access_path,
                                      previous=previous, keys=keys)
        except ValueError as error:
            self.status.set(str(error))
        except OSError:
            self.status.set('Could not save the shortcuts.')
        else:
            self.status.set('Shortcuts saved. Choose Quit from the tray icon, then open start.cmd to load them.')
        finally:
            self.current.set('')
        return previous


if __name__ == '__main__':
    root = tk.Tk()
    SettingsWindow(root)
    root.mainloop()
