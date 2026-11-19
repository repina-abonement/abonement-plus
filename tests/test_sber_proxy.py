import importlib.util
from pathlib import Path
import unittest
import tempfile
import subprocess
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('sber_proxy',Path(__file__).parents[1]/'deploy/protect-sber-callback.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class SberProxy(unittest.TestCase):
    def test_only_callback_logging_changes_and_all_server_blocks_preserved(self):
        text='server {\n    location / {\n        proxy_pass http://127.0.0.1:8000;\n    }\n}\n'
        protected=module.protect(text+text)
        self.assertEqual(protected.count('location = /api/sber/oauth/callback'),2)
        self.assertEqual(protected.count('access_log off;'),2)
        self.assertEqual(protected.count('location / {'),2)
        self.assertEqual(module.protect(protected),protected)

    def test_bad_proxy_config_is_restored_before_any_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config';original='server {\n    location / { proxy_pass http://127.0.0.1:8000; }\n}\n'
            path.write_text(original);path.chmod(0o600)
            with patch.object(module.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['nginx'])) as command:
                with self.assertRaises(SystemExit):module.main(path)
            self.assertEqual(path.read_text(),original)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            command.assert_called_once_with(['nginx','-t','-q'],check=True)

    def test_reload_only_after_validating_and_repeated_run_is_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config';path.write_text('server {\n    location / { proxy_pass http://127.0.0.1:8000; }\n}\n')
            with patch.object(module.subprocess,'run') as command:
                module.main(path);module.main(path)
            self.assertEqual([c.args[0] for c in command.call_args_list],[['nginx','-t','-q'],['systemctl','reload','nginx']])
