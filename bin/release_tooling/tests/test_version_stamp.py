"""
TestVersionStamp tests 'version_stamp.py'.
"""
#  Apache-2.0 license
#  Copyright (c) 2026 Asger Jon Vistisen
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

#  The folder of the release tooling and the root of the repository,
#  which a test may read and never write.
TOOLING = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(TOOLING))

PYPROJECT = """[project]
name = "jupyterminal"
version = "0.0.0"
requires-python = ">=3.12"
"""

PACKAGE = """\"\"\"The package.\"\"\"
__version__ = '0.0.0'
"""

README = """[![badge](https://example.org/0.0.0)](https://example.org)

# jupyterminal v0.0.0

A terminal emulator.
"""

#  As in the repository: comments that name the line, the line itself,
#  and lines that only use what it sets.
PKGBUILD = """# The release workflows write the line that starts with _tag=
pkgname=jupyterminal
_tag=0.0.0
pkgver=${_tag//-/}
source=("$pkgname::git+$url.git#tag=$_tag")
"""


class TestVersionStamp(unittest.TestCase):
  """
  The publish job of every button stamps the version it is about to
  publish into the files that show one: the build reads it from
  'pyproject.toml', the program says it and the README is headed by it.
  The package for Arch Linux fetches a release by it, and follows the
  releases alone. Every test works on a copy of that layout, never on
  the files of the repository.
  """

  def setUp(self) -> None:
    self.root = tempfile.mkdtemp(prefix='version-stamp-')
    self.addCleanup(shutil.rmtree, self.root, True)
    for parts, text in (
        (('pyproject.toml',), PYPROJECT),
        (('src', 'jupyterminal', '__init__.py'), PACKAGE),
        (('README.md',), README),
        (('packaging', 'arch', 'PKGBUILD'), PKGBUILD),
    ):
      self.write(text, *parts)
    os.makedirs(os.path.join(self.root, 'tests'))
    tooling = os.path.join(self.root, 'bin', 'release_tooling')
    os.makedirs(tooling)
    self.script = os.path.join(tooling, 'version_stamp.py')
    shutil.copy(os.path.join(TOOLING, 'version_stamp.py'), self.script)

  def write(self, text: str, *parts: str) -> None:
    path = os.path.join(self.root, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
      handle.write(text)

  def read(self, *parts: str) -> str:
    with open(os.path.join(self.root, *parts), encoding='utf-8') as handle:
      return handle.read()

  def stamp(self, version: str) -> tuple:
    """Run the script as the workflows do and return its exit code and
    what it printed."""
    env = dict(os.environ, VERSION_INFO=version)
    done = subprocess.run([sys.executable, '-B', self.script,
                           'VERSION_INFO'], env=env, cwd=self.root,
                          capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr

  def test_dev_version(self) -> None:
    code, output = self.stamp('1.2.3-dev4')
    self.assertEqual((code, output), (0, 'Stamped version: 1.2.3-dev4\n'))
    self.assertEqual(self.read('pyproject.toml'), str.replace(
      PYPROJECT, 'version = "0.0.0"', 'version = "1.2.3-dev4"'))
    self.assertEqual(self.read('src', 'jupyterminal', '__init__.py'),
                     str.replace(PACKAGE, "'0.0.0'", "'1.2.3-dev4'"))
    self.assertEqual(self.read('README.md'), str.replace(
      README, '# jupyterminal v0.0.0',
      '# jupyterminal v1.2.3-dev4 (development build)'))
    #  The package for Arch Linux goes on installing the last release.
    self.assertEqual(self.read('packaging', 'arch', 'PKGBUILD'), PKGBUILD)

  def test_candidate_and_release(self) -> None:
    code, output = self.stamp('1.2.3-rc0')
    self.assertEqual(code, 0, output)
    self.assertIn('\n# jupyterminal v1.2.3-rc0 (release candidate)\n',
                  self.read('README.md'))
    self.assertIn('version = "1.2.3-rc0"\n', self.read('pyproject.toml'))
    self.assertEqual(self.read('packaging', 'arch', 'PKGBUILD'), PKGBUILD)
    code, output = self.stamp('1.2.3')
    self.assertEqual(code, 0, output)
    self.assertIn('\n# jupyterminal v1.2.3\n', self.read('README.md'))
    self.assertIn('version = "1.2.3"\n', self.read('pyproject.toml'))
    #  The one line, and nothing of the comment or of the lines that use
    #  what it sets.
    self.assertEqual(self.read('packaging', 'arch', 'PKGBUILD'),
                     str.replace(PKGBUILD, '\n_tag=0.0.0\n',
                                 '\n_tag=1.2.3\n'))

  def test_a_file_without_its_line_stops_the_press(self) -> None:
    self.write(str.replace(PKGBUILD, '_tag=0.0.0\n', ''),
               'packaging', 'arch', 'PKGBUILD')
    code, output = self.stamp('1.2.3-rc1')       # which leaves it alone
    self.assertEqual(code, 0, output)
    code, output = self.stamp('1.2.3')
    self.assertEqual(code, 4)
    self.assertIn("Found no line starting with '_tag='", output)
    os.remove(os.path.join(self.root, 'packaging', 'arch', 'PKGBUILD'))
    code, output = self.stamp('1.2.3')
    self.assertEqual(code, 4)

  def test_nothing_to_stamp(self) -> None:
    code, output = self.stamp('  ')
    self.assertEqual(code, 2)
    self.assertEqual(self.read('pyproject.toml'), PYPROJECT)


class TestPackageForArch(unittest.TestCase):
  """
  The PKGBUILD of the repository installs a release the repository has
  reached, and gives it a version that pacman puts in the right place
  among the others.
  """

  @staticmethod
  def lines(*parts: str) -> list:
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as handle:
      return str.split(handle.read(), '\n')

  @staticmethod
  def place(version: str) -> tuple:
    """Where a version stands among the others: the development builds
    of a version come before its release candidates, and those before
    the release."""
    numbers, _, build = str.partition(version, '-')
    major, minor, micro = (int(number) for number in
                           str.split(numbers, '.'))
    for rank, kind in enumerate(('dev', 'rc')):
      if str.startswith(build, kind):
        return major, minor, micro, rank, int(build[len(kind):])
    return major, minor, micro, 2, 0

  def test_the_order_of_versions(self) -> None:
    versions = ['0.0.1-dev0', '0.0.1-dev1', '0.0.1-dev10', '0.0.1-rc0',
                '0.0.1-rc2', '0.0.1', '0.0.2-dev0', '0.1.0', '1.0.0-rc0']
    self.assertEqual(sorted(reversed(versions), key=self.place), versions)

  def test_the_tag_is_not_ahead_of_the_repository(self) -> None:
    """Only the release buttons write the tag, so it names the newest
    release, or, until there is one, the first development build: never
    a version the repository has not reached."""
    tags = [line for line in self.lines('packaging', 'arch', 'PKGBUILD')
            if str.startswith(line, '_tag=')]
    versions = [line for line in self.lines('pyproject.toml')
                if str.startswith(line, 'version = "')]
    self.assertEqual(len(tags), 1)
    self.assertEqual(len(versions), 1)
    tag = tags[0][len('_tag='):]
    version = versions[0][len('version = "'):-1]
    self.assertLessEqual(self.place(tag), self.place(version))

  def test_pacman_is_told_what_to_say(self) -> None:
    """Removing the package takes everything it installed; the script
    beside the PKGBUILD says so, and what is left to each user."""
    self.assertIn('install=$pkgname.install',
                  self.lines('packaging', 'arch', 'PKGBUILD'))
    path = os.path.join(ROOT, 'packaging', 'arch', 'jupyterminal.install')
    for hook, words in (
        ('post_install', ('menus', '--install-desktop-entry')),
        ('post_remove', ('gone', '~/.config/jupyterminal/')),
    ):
      done = subprocess.run(['bash', '-c', 'source "$0" && %s' % hook,
                             path], capture_output=True, text=True)
      self.assertEqual((done.returncode, done.stderr), (0, ''))
      for word in words:
        self.assertIn(word, done.stdout)

  def test_pacman_sorts_the_builds_before_their_release(self) -> None:
    """pacman takes '1.2.3dev4' and '1.2.3rc0' for older than '1.2.3',
    and '1.2.3.dev4' for newer: the name of a build is joined to the
    version without a full stop."""
    [line] = [line for line in self.lines('packaging', 'arch', 'PKGBUILD')
              if str.startswith(line, 'pkgver=')]
    for tag, pkgver in (('1.2.3-dev4', '1.2.3dev4'),
                        ('1.2.3-rc0', '1.2.3rc0'),
                        ('1.2.3', '1.2.3')):
      script = '_tag=%s\n%s\nprintf %%s "$pkgver"' % (tag, line)
      done = subprocess.run(['bash', '-c', script], capture_output=True,
                            text=True)
      self.assertEqual((done.returncode, done.stdout), (0, pkgver))
