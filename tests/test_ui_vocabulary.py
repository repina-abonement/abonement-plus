"""Keep the permanent UI terminology rule in new source changes."""
from pathlib import Path
import unittest

class InterfaceVocabulary(unittest.TestCase):
    def test_project_sources_use_current_terms(self):
        root=Path(__file__).resolve().parents[1]/'crm'
        violations=[]
        for path in root.rglob('*'):
            if path.suffix not in {'.py','.js','.html','.css'} or path.name=='uiwording.py':
                continue
            if 'сотруд' in path.read_text().lower():
                violations.append(str(path.relative_to(root)))
        self.assertEqual(violations, [])
