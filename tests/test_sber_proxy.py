import importlib.util
from pathlib import Path
import unittest
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
