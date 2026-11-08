import _bootstrap
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from background_runtime import WakeGesture, SingleInstance
import importlib.util
import importlib


class GestureTests(unittest.TestCase):
    def test_hold_release_and_hide_suppression(self):
        wake=WakeGesture()
        pose=[dict(number=1,gesture='ILoveYou')]
        answers=[wake.update(pose,'a',t/10) for t in range(10)]
        self.assertEqual(sum(answers),1)
        wake.suppress()
        self.assertFalse(any(wake.update(pose,'a',t/10) for t in range(10,20)))
        for t in range(20,27):wake.update([], 'a',t/10)
        self.assertEqual(sum(wake.update(pose,'a',t/10) for t in range(27,38)),1)

    def test_unknown_disabled_wrong_pose_and_changed_identity(self):
        pose=[dict(number=1,gesture='ILoveYou')]
        for mode in ('unknown','disabled','wrong','identity','hand'):
            wake=WakeGesture()
            for t in range(20):
                hands=pose if mode!='wrong' else [dict(number=1,gesture='Victory')]
                if mode=='hand':hands=[dict(number=1+t%2,gesture='ILoveYou')]
                identity=None if mode=='unknown' else str(t%2) if mode=='identity' else 'a'
                self.assertFalse(wake.update(hands,identity,t/10,enabled=mode!='disabled'),mode)

    def test_missing_frames_reset_hold(self):
        wake=WakeGesture()
        pose=[dict(number=1,gesture='ILoveYou')]
        for stamp in (0,.1,.2,2,2.1,2.2):self.assertFalse(wake.update(pose,'a',stamp))

    def test_releasing_with_a_different_pose_rearms(self):
        wake=WakeGesture();wake.suppress()
        for t in range(6):wake.update([dict(number=1,gesture='Victory')],'a',t/10)
        self.assertTrue(wake.armed)


class InstanceTests(unittest.TestCase):
    def test_activation_single_owner_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            with SingleInstance(folder) as primary:
                self.assertTrue(primary.primary)
                self.assertFalse(primary.take_activation())
                with SingleInstance(folder) as secondary:
                    self.assertFalse(secondary.primary)
                    secondary.activate()
                    self.assertTrue(primary.take_activation())
                    self.assertFalse(primary.take_activation())
                    self.assertFalse(secondary.take_activation())
            self.assertIsNone(primary.mutex)
            with SingleInstance(folder) as fresh:self.assertTrue(fresh.primary)

    def test_second_launch_activates_without_loading_camera_models(self):
        module=importlib.import_module('recognize')
        for arguments,activated in (([],True),(['--background'],False)):
            instance=MagicMock(primary=False)
            context=MagicMock();context.__enter__.return_value=instance
            with patch('background_runtime.SingleInstance',return_value=context), \
                 patch.object(module.enrollment,'load_models') as models, \
                 patch('sys.argv',['recognize.py']+arguments):
                self.assertEqual(module.main(),0)
                self.assertEqual(instance.activate.called,activated)
                models.assert_not_called()


class StartupTests(unittest.TestCase):
    def setUp(self):
        root = patch.object(self.startup, 'PROJECT_ROOT', Path(r'C:\Vision Control'))
        root.start()
        self.addCleanup(root.stop)
        exists = patch('pathlib.Path.is_file', return_value=True)
        exists.start()
        self.addCleanup(exists.stop)

    @classmethod
    def setUpClass(cls):
        stage=Path(__file__).resolve().parent.parent/'tools'/'startup.py'
        spec=importlib.util.spec_from_file_location('vision_startup',stage)
        cls.startup=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.startup)

    def test_command_uses_hidden_interpreter_and_background(self):
        command=self.startup.command()
        self.assertIn('pythonw.exe" -B ',command)
        self.assertTrue(command.endswith(' --background'))
        self.assertLessEqual(len(command),260)

    def test_enable_disable_touch_only_named_current_user_value(self):
        module=self.startup
        with patch.object(module,'current',return_value=None),patch.object(module,'winreg') as registry:
            module.set_enabled(True)
            registry.CreateKeyEx.assert_called_once_with(registry.HKEY_CURRENT_USER,module.KEY,0,registry.KEY_SET_VALUE)
            self.assertEqual(registry.SetValueEx.call_args.args[1],module.VALUE)
        with patch.object(module,'current',return_value=module.command()),patch.object(module,'winreg') as registry:
            module.set_enabled(False)
            self.assertEqual(registry.DeleteValue.call_args.args[1],module.VALUE)

    def test_unrelated_value_is_never_overwritten(self):
        with patch.object(self.startup,'current',return_value='another application'),patch.object(self.startup,'winreg') as registry:
            for enabled in (True,False):
                with self.assertRaises(ValueError):self.startup.set_enabled(enabled)
            registry.CreateKeyEx.assert_not_called()
            registry.DeleteValue.assert_not_called()


if __name__=='__main__':unittest.main()
