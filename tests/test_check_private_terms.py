"""scripts/check_private_terms.py: the commit-time guard against client names and local paths.
The terms used here are made up; the real ones live only in a gitignored file."""
import importlib.util
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, '..', 'scripts', 'check_private_terms.py')
spec = importlib.util.spec_from_file_location('check_private_terms', SCRIPT)
cpt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpt)

DRIVE = 'D:' + '\\'          # built up so this file itself has no local path


def hits(text, terms=()):
    return cpt.scan_lines(list(enumerate(text.splitlines(), 1)), cpt.patterns(terms))


class TestScan(unittest.TestCase):

    def test_term_matches_as_a_whole_word_any_case(self):
        self.assertEqual(hits('a\nreport for ACMEBANK clients\n', ['acmebank']),
                         [(2, 'private term')])
        self.assertEqual(hits('acmebanking and xacmebank', ['acmebank']), [])

    def test_local_paths(self):
        for text in [DRIVE + 'OneDrive' + '\\dev\\x', DRIVE + 'Users\\jo\\x',
                     'see /home/jo/x', 'see /Users/jo/x']:  # private-terms: allow
            self.assertEqual(hits(text), [(1, 'local path')], text)

    def test_placeholders_and_relative_paths_pass(self):
        for text in [DRIVE + 'Users\\<name>\\dev', '/home/<name>/x', 'docs/home/x',
                     '/path/to/repo', 'C:/venvs/x/python.exe']:
            self.assertEqual(hits(text), [], text)

    def test_allow_marker(self):
        self.assertEqual(hits('acmebank  # private-terms: allow', ['acmebank']), [])

    def test_hit_does_not_echo_the_term(self):
        self.assertNotIn('acmebank', repr(hits('acmebank', ['acmebank'])))


class TestTerms(unittest.TestCase):

    def test_sources_merge(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, '.private-terms'), 'w') as f:
                f.write('# comment\nalpha\n\nbeta  # trailing\n')
            other = os.path.join(root, 'other.txt')
            with open(other, 'w') as f:
                f.write('gamma\n')
            env = {'ORYX_PRIVATE_TERMS_FILE': other, 'ORYX_PRIVATE_TERMS': 'delta; alpha'}
            self.assertEqual(cpt.load_terms(root, env), ['alpha', 'beta', 'delta', 'gamma'])
            self.assertEqual(cpt.load_terms(os.path.join(root, 'none'), {}), [])
            with open(os.path.join(root, '.oryx-private-terms'), 'w') as f:
                f.write('epsilon\n')
            self.assertEqual(cpt.load_terms(os.path.join(root, 'none'), {'HOME': root}),
                             ['epsilon'])


class TestStagedDiff(unittest.TestCase):

    def test_only_added_lines_count(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                subprocess.run(['git', '-C', root, '-c', 'user.name=t', '-c',
                                'user.email=t@t', '-c', 'core.autocrlf=false'] + list(args),
                               check=True, capture_output=True)
            git('init', '-q')
            path = os.path.join(root, 'a.md')
            with open(path, 'w') as f:
                f.write('old acmebank line\nkeep\n')
            git('add', 'a.md')
            git('commit', '-q', '-m', 'x')
            with open(path, 'w') as f:
                f.write('old acmebank line\nkeep\nnew acmebank line\n')
            git('add', 'a.md')
            lines = cpt.staged_lines(root)
            self.assertEqual(lines, {'a.md': [(3, 'new acmebank line')]})
            self.assertEqual(cpt.scan_lines(lines['a.md'], cpt.patterns(['acmebank'])),
                             [(3, 'private term')])


if __name__ == '__main__':
    unittest.main()
