"""Manage only this user's explicitly named Vision Control login entry."""
import _bootstrap
import argparse
import subprocess
import winreg
from project_paths import PROJECT_ROOT

KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE='Vision Control'


def command():
    executable=PROJECT_ROOT/'.venv'/'Scripts'/'pythonw.exe'
    script=PROJECT_ROOT/'src'/'recognize.py'
    if not executable.is_file() or not script.is_file():raise FileNotFoundError('Application or Python environment missing.')
    value=subprocess.list2cmdline([str(executable),'-B',str(script),'--background'])
    if len(value)>260:raise ValueError('Startup command exceeds the Windows Run length limit.')
    return value


def current():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY) as key:
            return winreg.QueryValueEx(key,VALUE)[0]
    except FileNotFoundError:return None


def set_enabled(enabled):
    expected=command()
    existing=current()
    if existing is not None and existing!=expected:
        raise ValueError('A different startup entry uses this name. It was kept unchanged.')
    if enabled:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,KEY,0,winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key,VALUE,0,winreg.REG_SZ,expected)
    elif existing is not None:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY,0,winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key,VALUE)


def main():
    parser=argparse.ArgumentParser(description='Windows login startup for Vision Control')
    parser.add_argument('action',choices=('status','enable','disable'),nargs='?',default='status')
    args=parser.parse_args()
    if args.action!='status':set_enabled(args.action=='enable')
    print('Windows login startup: '+('ON' if current()==command() else 'OFF'))


if __name__=='__main__':main()
