"""Revision reads work when Git refuses a service user's checkout ownership."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crm import erpsinglerecoveryweb as web


class DeploymentRevision(unittest.TestCase):
    sha = 'a' * 40

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git = self.root / '.git'
        self.git.mkdir()

    def test_loose_branch_revision_does_not_need_git_executable(self):
        (self.git / 'HEAD').write_text('ref: refs/heads/main\n')
        (self.git / 'refs/heads').mkdir(parents=True)
        (self.git / 'refs/heads/main').write_text(self.sha+'\n')
        self.assertEqual(web._metadata_commit(self.root), self.sha)
        with patch.object(web, '_metadata_commit', return_value=self.sha), patch.object(web.subprocess, 'check_output', side_effect=FileNotFoundError) as command:
            self.assertEqual(web._checkout_commit(), self.sha)
            command.assert_not_called()

    def test_packed_branch_and_detached_head_are_supported(self):
        (self.git / 'HEAD').write_text('ref: refs/heads/main\n')
        (self.git / 'packed-refs').write_text('# pack-refs\n'+self.sha+' refs/heads/main\n')
        self.assertEqual(web._metadata_commit(self.root), self.sha)
        (self.git / 'HEAD').write_text(self.sha+'\n')
        self.assertEqual(web._metadata_commit(self.root), self.sha)

    def test_worktree_reads_its_common_reference_metadata(self):
        (self.git / 'HEAD').write_text('ref: refs/heads/main\n')
        (self.git / 'packed-refs').write_text(self.sha+' refs/heads/main\n')
        linked = self.git / 'worktrees/preview'
        linked.mkdir(parents=True)
        (linked / 'HEAD').write_text('ref: refs/heads/main\n')
        (linked / 'commondir').write_text('../..\n')
        checkout = self.root / 'preview'
        checkout.mkdir()
        (checkout / '.git').write_text('gitdir: ../.git/worktrees/preview\n')
        self.assertEqual(web._metadata_commit(checkout), self.sha)

    def test_invalid_references_never_become_revision_headers(self):
        for value in ('ref: refs/../../outside', 'ref: /outside', 'not-a-commit', 'b'*40+'\nInjected: header'):
            (self.git / 'HEAD').write_text(value)
            self.assertIsNone(web._metadata_commit(self.root))


if __name__ == '__main__':
    unittest.main()
