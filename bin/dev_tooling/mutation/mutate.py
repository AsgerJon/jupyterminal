"""
A mutation run of a jupyterminal package must survive anything short of the
disk going away: a runner that dies, a session that ends, a mutant that
hangs. Every result is appended to a file in this folder the moment it
is known, the files of a run carry the package and the start time in
their names, every mutant has an ID that is the same on every run, and
a runner started on an unfinished run skips every mutant already
recorded. The source under 'src/' is never mutated: each mutant is
written into a copy of the whole 'src' tree under the run's own
'runs/<package>_<stamp>_sandbox/' beside this file, and a copy of
'tests/' and of 'pyproject.toml', taken when the runner starts, runs
against it there. The working tree may therefore change while a run is
under way.

  python bin/dev_tooling/mutation/mutate.py list --package jupyterminal.term
  python bin/dev_tooling/mutation/mutate.py check --package jupyterminal.term --id ID,ID
  python bin/dev_tooling/mutation/mutate.py run --package jupyterminal.term --jobs 4
  python bin/dev_tooling/mutation/mutate.py run --package jupyterminal.term --pass 1
  python bin/dev_tooling/mutation/mutate.py run --package jupyterminal --mark "not live"
  python bin/dev_tooling/mutation/mutate.py show --package jupyterminal.term --id ID
  python bin/dev_tooling/mutation/mutate.py carry --package jupyterminal.term
  python bin/dev_tooling/mutation/mutate.py report --package jupyterminal.term

Implementation
--------------

The mutants of a file are found by walking its syntax tree, in source
order, and each is a closure that changes one node of that tree; a
mutant is applied by parsing the file again, walking it the same way,
and calling the closure with the same index. The ID of a mutant is
'file:line:column:kind:variant', with the file relative to the package
and the variant counting the mutations of one kind at one position, so
the ID holds for as long as the source does. The kinds are listed on
'_describeKinds' below.

A run is the files 'runs/<package>_<YYYYMMDD-HHMMSS>_<kind>' beside this
file, a folder the repo ignores:
'meta.json' with the mutant table, 'results.jsonl' with one line per
finished mutant, 'attempts.jsonl' with one line per started mutant,
'progress.txt' with a single line rewritten after every start and
finish, 'survivors.md' with the report, and 'verdicts.json' and
'notes.md', written by hand, which the report reads. A result line
carries the pass it belongs to: pass 0 is the sweep over every mutant,
pass 1 and up rerun the mutants whose latest result is 'survived'. The
latest line of a mutant is its current result. A mutant started twice
in one pass without a result crashed the runner twice and is recorded
as 'crashed'; one started once without a result is rerun alone before
the rest, so a crash names its mutant. Ctrl-C ends a run the same way:
the queued mutants never start, the running ones keep their attempt
and lose their result, and the same command resumes. 'meta.json' also
holds a hash of every file of the package, and a run whose source has
changed since is refused, because an ID is a position and would name
another mutant; '--fresh' sweeps the current source.

The tests run as a subprocess of the project environment's Python, in
the sandbox, with 'pytest' pointed at the copy of 'tests/', its
configured 'addopts' and 'pythonpath' replaced so coverage is off and
the sandbox copy of 'src' is imported. The temporary files of the tests
and the runtime directories of the shells they start stay in the
sandbox as well. Python keeps no bytecode of the copy of 'src', so a
mutant can never be run from the bytecode of the source it replaced:
every package folder of the copy holds a file named '__pycache__' where
the folder of that name would go. The copy of 'tests' does not change
during a run and keeps its bytecode, pytest's rewritten asserts
included, which is most of what starting pytest costs for a suite of
any size.

A sweep must never take the machine it runs on away from whoever is
using it. Many mutants turn a loop into one that never ends, and some
of those fill a list while they go round; the mutants of one loop are
neighbours in the table, so every job may be running such a mutant at
the same moment. (A sweep with twelve jobs and a limit of 8 GiB each
ran a 32 GiB desktop out of memory twice, on the loops of
'ui/render.py'.) Hence four limits. No test process may map more
memory than 'MEMORY_LIMIT', about three times what a run of the suite
needs, so such a mutant ends in a MemoryError within seconds; '--memory'
sets it, and jobs times that limit is the most a sweep can take. No
mutant is started while the machine has less than 'MEMORY_FLOOR'
available: the runner waits. Should it fall below half of that all the
same, every test process then running is ended on the spot, and its
mutant gets the result 'error' with the reason, to be run again with
'--ids'. No single test may take longer than 'TEST_TIMEOUT' seconds
('--test-timeout'), so a mutant that just loops is killed by the first
test that meets it. And the runner gives itself the lowest priority,
which the tests inherit. The priority costs nothing on a machine that
is not busy, and the limit on memory is the one that decides how many
jobs there may be: eight of them, at the limit all at once, take 12 GiB.
More jobs help more than one would think, because a suite that drives a
user interface waits on timers for much of its time.

A runner that is ended from outside leaves its run as resumable as one
ended by Ctrl-C, with one difference: the tests it had started run on to
their end, in the sandboxes the resume is going to write into. End them
as well, after the runner, never before it: a test process that dies
while the runner lives is a result, and a wrong one. (A runner started
in the background by another program may have the interrupt blocked;
then SIGTERM is the way to end it.)
Exit code 0 or 5 is 'survived', 1 to 3 is 'killed', a timeout is
'timeout' and anything else is 'error'. The baseline runs on the
pristine copy before any run file is written; a baseline that fails
refuses the run, and so does one that collects no tests (exit code 5),
since a sweep against no tests marks every mutant survived and
measures nothing: until the first test exists the map is the listing
of 'list --verbose', not a sweep. The timeout is ten times the
baseline, at least twenty seconds, unless given. Each worker has a
sandbox of its own, so jobs run side by side.

The suite of jupyterminal starts real shells, and those tests are slow:
they carry the pytest marker 'live', and 'tests/conftest.py' runs them
last. '--mark' passes a marker expression on to pytest, so a pass with
'--mark "not live"' measures the mutants against the fast tests alone.
Whatever such a pass kills is killed by the whole suite too, so the
quick way through a package is a sweep with '--mark "not live"', then
'--pass 1' without a mark, which reruns its survivors against
everything. A mutant is a survivor of the suite only once a pass
without a mark has let it through.

The suite stops at the first failure, so what is likely to fail runs
first. A mutant is most often killed by the test that killed the mutant
before it: in a file of a few thousand mutants that held for six in ten,
and for nearly eight in ten the test was one of the last three. So the
runner remembers which test killed the mutant of which line, and before
a mutant it names the tests that killed the mutants nearest to it in
the same source file. In a sweep with several jobs that guess is right
only half the time, because the results of the neighbours still under
way are not known; so after those tests come the test files that killed
the most of the hundred mutants around it, where the test that kills is
then found nearly nine times in ten among the first three, and last the
test files that have killed the most mutants of the whole source file.
The plugin 'mutate_order.py', written into every sandbox, moves all of
them to the front in that order. The order changes how soon a mutant is
killed, never whether.

Slow is a failure too. A mutant that turns a test of a tenth of a
second into one that never ends has been noticed, and there is nothing
to wait for. The baseline run notes what every test takes, and under a
mutant a test may take 'SLOW_FACTOR' times that and 'SLOW_SLACK'
seconds: when it is not over by then the run ends, and the mutant is
killed by that test, with a line that starts 'SLOW' and says what the
test takes untouched. The untouched source has to get through a second
baseline run under these limits, or none are used.

'check' takes one ID or several, separated by commas, runs them against
the tests as they are in the working tree at that moment, in a sandbox
of its own, and records nothing: it is the way to try a new test
against the mutants it is meant to kill, also while a run is under way.
With '--tests' it runs only the test files named, which is all it takes
to see that a new test kills a survivor, and takes a second, not a
minute.

An ID is a position, so an edit renames every mutant below it, and the
verdicts of a run, which are kept by ID, would be lost with the next
sweep. What an edit elsewhere leaves alone is the key of a mutant: its
file, the function or class it is in, what it changes, the line of
source it is on with the indentation taken off, where on that line, and
which one it is among the mutants that agree in all of that. The mutant
table of a run holds the key of every mutant, and 'carry' gives the
survivors of the latest run the verdicts that an earlier run has for
the mutants of the same key, renaming the IDs a reason mentions as well.
A verdict was written about the code around its mutant, though, and
that may be other code by now: the table also holds a hash of the
function around every mutant, and a verdict carried into a function
that has changed is marked 'review', which the report shows until
somebody has read the reason against the new code and taken the mark
out. A verdict whose mutant has no counterpart is listed and left
behind: its line was changed, and whether the reason still holds is a
new question.
"""
#  Apache-2.0 license
#  Copyright (c) 2026 Asger Jon Vistisen
from __future__ import annotations

import argparse
import ast
import bisect
import difflib
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SRC = os.path.join(REPO, 'src')
TESTS = os.path.join(REPO, 'tests')
PYPROJECT = os.path.join(REPO, 'pyproject.toml')
RUNS = os.path.join(HERE, 'runs')
#  The tests run on the project environment and nothing else.
PYTHON = os.path.expanduser('~/miniforge3/envs/jupyterminal_env/bin/python')
IGNORE_CACHE = shutil.ignore_patterns('__pycache__')
#  The most a test process may map, in kibibytes, as 'ulimit -v' takes
#  it. One run of the fast tests maps 430 MiB at its peak (measured), so
#  this is three and a half times that. '--memory' sets it.
MEMORY_LIMIT = 1536 * 1024
#  What the machine must have available, in kibibytes, for a mutant to
#  be started.
MEMORY_FLOOR = 4 * 1024 * 1024
#  The seconds one test may take before pytest ends the run of a mutant,
#  which counts as a kill: a mutant that hangs is caught by that test.
#  '--test-timeout' sets it.
TEST_TIMEOUT = 60
#  How many tests, and after them how many test files, are moved to the
#  front for a mutant, and how many of the kills nearest to a mutant are
#  looked at for them: the nearest few for the tests, all of them for
#  the files.
FIRST_TESTS = 6
FIRST_FILES = 4
FIRST_NEAR = 48
FIRST_SCAN = 100
#  A test that takes this many times what it takes on the untouched
#  source, and this many seconds on top, has noticed the mutant: slow is
#  a failure too. The seconds allow for what the first test of a run
#  pays for everybody (imports, the application object) and for a busy
#  machine. 'TEST_TIMEOUT' stays the most any test may take.
SLOW_FACTOR = 10.0
SLOW_SLACK = 4.0
#  The environment variables of the pytest plugin that is written into
#  every sandbox: the tests and test files to run first, one to a line;
#  where to write what every test took; where to read what every test
#  may take. And the file, beside the plugin, in which it names the test
#  that is running.
FIRST_KEY = 'MUTATE_FIRST'
TIMES_KEY = 'MUTATE_TIMES'
LIMITS_KEY = 'MUTATE_LIMITS'
SLOW_FILE = 'slow.txt'
ORDER_MODULE = 'mutate_order'
ORDER_PLUGIN = '''"""
Written by mutate.py.

The tests and the test files named in MUTATE_FIRST, one to a line, run
before the others, in the order given.

With MUTATE_TIMES, what every test took is written to that file when
the session ends: {test: seconds}.

With MUTATE_LIMITS, a file of {test: [limit, normal]} in seconds, a test
that is not over when its limit is ends the whole run on the spot with
exit code 1, and so does a test that hangs for good, by the timeout of
pytest itself. Either way 'slow.txt' beside this file still names the
test: it is written when a test starts and removed when it is over.
"""
import faulthandler
import json
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SLOW = os.path.join(_HERE, 'slow.txt')
_limits = {}
_times = {}
#  Where the report of a test over its limit goes: nobody reads it.
_void = open(os.devnull, 'w')


def pytest_configure(config):
  path = os.environ.get('MUTATE_LIMITS', '')
  if path:
    with open(path, encoding='utf-8') as handle:
      _limits.update(json.load(handle))


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
  names = [n for n in os.environ.get('MUTATE_FIRST', '').split('\\n') if n]
  rank = {name: i for i, name in enumerate(names)}
  last = len(names)
  items.sort(key=lambda item: min(
      rank.get(item.nodeid, last),
      rank.get(item.nodeid.split('::')[0], last),
  ))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
  if not _limits:
    return
  limit, normal = _limits.get(item.nodeid, (0, 0))
  with open(_SLOW, 'w', encoding='utf-8') as handle:
    handle.write('%s\\n%s\\n%s\\n' % (item.nodeid, limit, normal))
  if limit:
    #  In place of the timer pytest has just set for this test; pytest
    #  cancels whichever is set when the test is over.
    faulthandler.dump_traceback_later(limit, exit=True, file=_void)


def pytest_runtest_logfinish(nodeid, location):
  if _limits:
    faulthandler.cancel_dump_traceback_later()
    try:
      os.remove(_SLOW)
    except OSError:
      pass


def pytest_runtest_logreport(report):
  _times[report.nodeid] = _times.get(report.nodeid, 0.0) + report.duration


def pytest_sessionfinish(session):
  path = os.environ.get('MUTATE_TIMES', '')
  if path:
    with open(path, 'w', encoding='utf-8') as handle:
      json.dump(_times, handle)
'''

SURVIVED = 'survived'
KILLED = 'killed'
TIMEOUT = 'timeout'
ERROR = 'error'
CRASHED = 'crashed'
STATUSES = (KILLED, SURVIVED, TIMEOUT, ERROR, CRASHED)


# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#  FILES  # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

def readText(path: str) -> str:
  handle = None
  try:
    handle = open(path, 'r', encoding='utf-8')
  except OSError as osError:
    raise osError
  else:
    return handle.read()
  finally:
    if handle is not None:
      handle.close()


def writeText(path: str, text: str) -> None:
  """
  The text goes to a sibling file first and is moved into place, so a
  reader never sees a half-written file.
  """
  tmp = '%s.tmp' % path
  handle = None
  try:
    handle = open(tmp, 'w', encoding='utf-8')
  except OSError as osError:
    raise osError
  else:
    handle.write(text)
    handle.flush()
    os.fsync(handle.fileno())
  finally:
    if handle is not None:
      handle.close()
  os.replace(tmp, path)


def appendLine(path: str, line: str) -> None:
  handle = None
  try:
    handle = open(path, 'a', encoding='utf-8')
  except OSError as osError:
    raise osError
  else:
    handle.write(line)
    handle.write('\n')
    handle.flush()
    os.fsync(handle.fileno())
  finally:
    if handle is not None:
      handle.close()


def readJsonLines(path: str) -> list:
  """A line that does not parse, as the last line of a file cut short
  could be, is skipped."""
  if not os.path.exists(path):
    return []
  out = []
  for line in readText(path).splitlines():
    if not line.strip():
      continue
    try:
      out.append(json.loads(line))
    except ValueError:
      continue
  return out


def readJson(path: str, fallback: object) -> object:
  if not os.path.exists(path):
    return fallback
  return json.loads(readText(path))


# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#  MUTANTS  # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

class Mutant:
  """
  One mutation of one source file: where it is, what it does, and the
  change it makes to the parsed tree it was collected from.
  """

  __slots__ = (
    'file', 'line', 'col', 'kind', 'variant', 'description', 'apply',
    'scope', 'source', 'offset', 'nth', 'around',
  )

  def __init__(
      self,
      file: str,
      line: int,
      col: int,
      kind: str,
      variant: int,
      description: str,
      apply: object,
  ) -> None:
    self.file = file
    self.line = line
    self.col = col
    self.kind = kind
    self.variant = variant
    self.description = description
    self.apply = apply
    #  Set by '_place' once the whole file has been walked.
    self.scope = ''
    self.source = ''
    self.offset = 0
    self.nth = 0
    self.around = ''

  @property
  def id(self) -> str:
    return '%s:%d:%d:%s:%d' % (
      self.file, self.line, self.col, self.kind, self.variant,
    )

  @property
  def key(self) -> list:
    """What the mutant is, wherever an edit above it moves it to."""
    return [
      self.file, self.scope, self.kind, self.variant, self.description,
      self.source, self.offset, self.nth,
    ]

  def record(self) -> dict:
    return dict(
        id=self.id,
        file=self.file,
        line=self.line,
        col=self.col,
        kind=self.kind,
        variant=self.variant,
        description=self.description,
        key=self.key,
        around=self.around,
    )


_SYMBOLS = {
  ast.Eq: '==', ast.NotEq: '!=', ast.Lt: '<', ast.LtE: '<=',
  ast.Gt: '>', ast.GtE: '>=', ast.Is: 'is', ast.IsNot: 'is not',
  ast.In: 'in', ast.NotIn: 'not in', ast.And: 'and', ast.Or: 'or',
  ast.Add: '+', ast.Sub: '-', ast.Mult: '*', ast.Div: '/',
  ast.FloorDiv: '//', ast.Pow: '**',
}

_COMPARE = {
  ast.Eq: (ast.NotEq,),
  ast.NotEq: (ast.Eq,),
  ast.Lt: (ast.LtE, ast.Gt),
  ast.LtE: (ast.Lt, ast.GtE),
  ast.Gt: (ast.GtE, ast.Lt),
  ast.GtE: (ast.Gt, ast.LtE),
  ast.Is: (ast.IsNot,),
  ast.IsNot: (ast.Is,),
  ast.In: (ast.NotIn,),
  ast.NotIn: (ast.In,),
}

_BINARY = {
  ast.Add: ast.Sub,
  ast.Sub: ast.Add,
  ast.Mult: ast.Add,
  ast.Div: ast.Mult,
  ast.FloorDiv: ast.Div,
  ast.Pow: ast.Mult,
}

_BUILTINS = {'min': 'max', 'max': 'min', 'any': 'all', 'all': 'any'}

_DELETABLE = (
  ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Raise,
  ast.Continue, ast.Break, ast.Delete,
)

_STATEMENT_LISTS = ('body', 'orelse', 'finalbody')

_SKIP_TARGETS = ('__all__', '__slots__')

_SCOPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _describeKinds() -> str:
  return """
  cmp      a comparison operator replaced: == and != swap, < becomes
           <= or >, and so on; is, is not, in and not in swap.
  bool     'and' and 'or' swap.
  not      a 'not' is dropped.
  flag     'True' and 'False' swap.
  num      an integer literal is one more or one less.
  str      a string literal of one line becomes '' or, if empty, 'XX'.
  binop    an arithmetic operator replaced: + and - swap, * becomes +,
           / becomes *, // becomes /, ** becomes *.
  aug      the same for an augmented assignment.
  ret      a returned value becomes None.
  cond     the condition of an if or a conditional expression becomes
           True or False; of a while, False.
  loopctl  'continue' and 'break' swap.
  builtin  min and max swap, any and all swap.
  slice    a one-sided slice moves its bound to the other side.
  tup      one element is dropped from a tuple that is an argument of
           isinstance or issubclass, the right side of in or not in,
           the value of an assignment, or the types of an except.
  del      a statement inside a function body is replaced by pass: a
           call, an assignment, a raise, a continue, a break or a del.
  """


def _replacing(parent: ast.AST, field: str, index: object, new: ast.AST):
  def apply() -> None:
    if index is None:
      setattr(parent, field, new)
    else:
      getattr(parent, field)[index] = new

  return apply


def _setting(node: ast.AST, field: str, value: object):
  def apply() -> None:
    setattr(node, field, value)

  return apply


def _settingItem(node: ast.AST, field: str, index: int, value: object):
  def apply() -> None:
    getattr(node, field)[index] = value

  return apply


def _dropping(node: ast.AST, field: str, index: int):
  def apply() -> None:
    del getattr(node, field)[index]

  return apply


def _isDocstring(scope: ast.AST, field: str, items: list) -> bool:
  if field != 'body' or not isinstance(scope, _SCOPES):
    return False
  if not items or not isinstance(items[0], ast.Expr):
    return False
  value = items[0].value
  return isinstance(value, ast.Constant) and isinstance(value.value, str)


def _isNoneConstant(node: ast.AST) -> bool:
  return isinstance(node, ast.Constant) and node.value is None


def _namesTarget(node: ast.AST, names: tuple) -> bool:
  if isinstance(node, ast.Assign):
    targets = node.targets
  elif isinstance(node, ast.AnnAssign):
    targets = [node.target]
  else:
    return False
  for target in targets:
    if isinstance(target, ast.Name) and target.id in names:
      return True
  return False


def _tupleContext(parent: ast.AST, field: str, index: object) -> bool:
  if isinstance(parent, ast.Call) and field == 'args':
    func = parent.func
    if isinstance(func, ast.Name):
      return func.id in ('isinstance', 'issubclass')
    return False
  if isinstance(parent, ast.Compare) and field == 'comparators':
    return isinstance(parent.ops[index], (ast.In, ast.NotIn))
  if isinstance(parent, (ast.Assign, ast.AnnAssign)) and field == 'value':
    return True
  if isinstance(parent, ast.ExceptHandler) and field == 'type':
    return True
  return False


class _Collector:
  """
  The walk over one file, collecting a mutant for every change the
  kinds above allow, in source order.
  """

  def __init__(self, file: str) -> None:
    self.file = file
    self.out = []
    self.counts = dict()

  def add(
      self,
      node: ast.AST,
      kind: str,
      description: str,
      apply: object,
  ) -> None:
    key = (node.lineno, node.col_offset, kind)
    if key in self.counts:
      variant = self.counts[key]
    else:
      variant = 0
    self.counts[key] = variant + 1
    mutant = Mutant(
        self.file, node.lineno, node.col_offset, kind, variant,
        description, apply,
    )
    self.out.append(mutant)

  @staticmethod
  def _skipped(node: ast.AST) -> bool:
    if isinstance(node, (ast.arguments, ast.Import, ast.ImportFrom)):
      return True
    if isinstance(node, ast.If):
      test = node.test
      if isinstance(test, ast.Name) and test.id == 'TYPE_CHECKING':
        return True
    return _namesTarget(node, _SKIP_TARGETS)

  def walk(
      self,
      node: ast.AST,
      parent: ast.AST,
      field: str,
      index: object,
      depth: int,
  ) -> None:
    if self._skipped(node):
      return
    self._collect(node, parent, field, index, depth)
    if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
      fields = ['body']
    elif isinstance(node, ast.AsyncFunctionDef):
      fields = ['body']
    elif isinstance(node, ast.Lambda):
      fields = ['body']
    else:
      fields = [name for name, _ in ast.iter_fields(node)]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
      depth += 1
    if isinstance(node, ast.Lambda):
      depth += 1
    for name in fields:
      value = getattr(node, name)
      if isinstance(value, list):
        first = 1 if _isDocstring(node, name, value) else 0
        for i, item in enumerate(value):
          if i < first:
            continue
          if isinstance(item, ast.AST):
            self.walk(item, node, name, i, depth)
      elif isinstance(value, ast.AST):
        self.walk(value, node, name, None, depth)

  def _collect(
      self,
      node: ast.AST,
      parent: ast.AST,
      field: str,
      index: object,
      depth: int,
  ) -> None:
    if isinstance(node, ast.Compare):
      self._compare(node)
    elif isinstance(node, ast.BoolOp):
      self._boolOp(node)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
      apply = _replacing(parent, field, index, node.operand)
      self.add(node, 'not', "drop 'not'", apply)
    elif isinstance(node, ast.Constant):
      self._constant(node)
    elif isinstance(node, ast.BinOp):
      self._binary(node, 'binop')
    elif isinstance(node, ast.AugAssign):
      self._binary(node, 'aug')
    elif isinstance(node, ast.Return):
      self._return(node)
    elif isinstance(node, (ast.If, ast.IfExp)):
      self._condition(node, True)
      self._condition(node, False)
    elif isinstance(node, ast.While):
      self._condition(node, False)
    elif isinstance(node, ast.Continue):
      new = ast.copy_location(ast.Break(), node)
      apply = _replacing(parent, field, index, new)
      self.add(node, 'loopctl', "'continue' -> 'break'", apply)
    elif isinstance(node, ast.Break):
      new = ast.copy_location(ast.Continue(), node)
      apply = _replacing(parent, field, index, new)
      self.add(node, 'loopctl', "'break' -> 'continue'", apply)
    elif isinstance(node, ast.Call):
      self._call(node)
    elif isinstance(node, ast.Slice):
      self._slice(node)
    elif isinstance(node, ast.Tuple):
      self._tuple(node, parent, field, index)
    if depth > 0 and isinstance(node, _DELETABLE):
      self._delete(node, parent, field, index)

  def _compare(self, node: ast.Compare) -> None:
    for i, op in enumerate(node.ops):
      if type(op) not in _COMPARE:
        continue
      for new in _COMPARE[type(op)]:
        description = "'%s' -> '%s'" % (_SYMBOLS[type(op)], _SYMBOLS[new])
        self.add(node, 'cmp', description, _settingItem(node, 'ops', i, new()))

  def _boolOp(self, node: ast.BoolOp) -> None:
    if isinstance(node.op, ast.And):
      new = ast.Or
    else:
      new = ast.And
    description = "'%s' -> '%s'" % (_SYMBOLS[type(node.op)], _SYMBOLS[new])
    self.add(node, 'bool', description, _setting(node, 'op', new()))

  def _constant(self, node: ast.Constant) -> None:
    value = node.value
    if isinstance(value, bool):
      description = '%r -> %r' % (value, not value)
      self.add(node, 'flag', description, _setting(node, 'value', not value))
    elif isinstance(value, int):
      for new in (value + 1, value - 1):
        description = '%r -> %r' % (value, new)
        self.add(node, 'num', description, _setting(node, 'value', new))
    elif isinstance(value, str):
      if '\n' in value:
        return
      new = '' if value else 'XX'
      description = '%r -> %r' % (value, new)
      self.add(node, 'str', description, _setting(node, 'value', new))

  def _binary(self, node: ast.AST, kind: str) -> None:
    if type(node.op) not in _BINARY:
      return
    new = _BINARY[type(node.op)]
    description = "'%s' -> '%s'" % (_SYMBOLS[type(node.op)], _SYMBOLS[new])
    self.add(node, kind, description, _setting(node, 'op', new()))

  def _return(self, node: ast.Return) -> None:
    if node.value is None or _isNoneConstant(node.value):
      return
    new = ast.copy_location(ast.Constant(None), node.value)
    self.add(node, 'ret', 'return None', _setting(node, 'value', new))

  def _condition(self, node: ast.AST, value: bool) -> None:
    new = ast.copy_location(ast.Constant(value), node.test)
    self.add(node, 'cond', 'condition -> %r' % value, _setting(node, 'test', new))

  def _call(self, node: ast.Call) -> None:
    func = node.func
    if not isinstance(func, ast.Name) or func.id not in _BUILTINS:
      return
    new = _BUILTINS[func.id]
    description = "'%s' -> '%s'" % (func.id, new)
    self.add(node, 'builtin', description, _setting(func, 'id', new))

  def _slice(self, node: ast.Slice) -> None:
    if node.step is not None:
      return
    if node.lower is None and node.upper is not None:
      def apply() -> None:
        node.lower, node.upper = node.upper, None

      self.add(node, 'slice', '[:b] -> [b:]', apply)
    elif node.upper is None and node.lower is not None:
      def apply() -> None:
        node.lower, node.upper = None, node.lower

      self.add(node, 'slice', '[a:] -> [:a]', apply)

  def _tuple(
      self,
      node: ast.Tuple,
      parent: ast.AST,
      field: str,
      index: object,
  ) -> None:
    if len(node.elts) < 2:
      return
    for element in node.elts:
      if isinstance(element, ast.Starred):
        return
    if not _tupleContext(parent, field, index):
      return
    for i, element in enumerate(node.elts):
      description = 'drop %s' % ast.unparse(element)
      self.add(node, 'tup', description, _dropping(node, 'elts', i))

  def _delete(
      self,
      node: ast.AST,
      parent: ast.AST,
      field: str,
      index: object,
  ) -> None:
    if field not in _STATEMENT_LISTS or index is None:
      return
    if isinstance(node, ast.Expr):
      kinds = (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)
      if not isinstance(node.value, kinds):
        return
    if isinstance(node, ast.AnnAssign) and node.value is None:
      return
    new = ast.copy_location(ast.Pass(), node)
    source = ast.unparse(node).splitlines()[0]
    description = 'delete: %s' % source[:60]
    self.add(node, 'del', description, _replacing(parent, field, index, new))


def _scopeTable(tree: ast.AST, text: str) -> list:
  """(first line, last line, qualified name, hash of the source) of
  every class and function of 'tree', an inner one after the one around
  it."""
  out = []

  def walk(node: ast.AST, prefix: str) -> None:
    for child in ast.iter_child_nodes(node):
      name = prefix
      if isinstance(child, _SCOPES) and not isinstance(child, ast.Module):
        name = '%s.%s' % (prefix, child.name) if prefix else child.name
        source = ast.get_source_segment(text, child)
        digest = hashlib.sha256(source.encode('utf-8')).hexdigest()[:16]
        out.append((child.lineno, child.end_lineno, name, digest))
      walk(child, name)

  walk(tree, '')
  return out


def _place(mutants: list, tree: ast.AST, text: str) -> None:
  """Gives every mutant of a file the parts of its key that the walk
  does not know: the scope it is in, its line of source, and which one
  it is among the mutants that are alike in everything else."""
  lines = text.splitlines()
  table = _scopeTable(tree, text)
  seen = dict()
  for mutant in mutants:
    line = lines[mutant.line - 1]
    for first, last, name, digest in table:
      if first <= mutant.line <= last:
        mutant.scope = name
        mutant.around = digest
    mutant.source = line.strip()
    mutant.offset = mutant.col - (len(line) - len(line.lstrip()))
    alike = (
      mutant.scope, mutant.kind, mutant.variant, mutant.description,
      mutant.source, mutant.offset,
    )
    mutant.nth = seen.get(alike, 0)
    seen[alike] = mutant.nth + 1


def collectFile(text: str, file: str) -> tuple:
  """Returns the tree of 'text' and its mutants, bound to that tree."""
  tree = ast.parse(text)
  collector = _Collector(file)
  collector.walk(tree, None, None, None, 0)
  _place(collector.out, tree, text)
  return tree, collector.out


def packageDir(package: str) -> str:
  return os.path.join(SRC, *package.split('.'))


def packageFiles(package: str) -> list:
  """The '.py' files of the package and its subpackages, relative to
  the package directory, in a fixed order."""
  root = packageDir(package)
  out = []
  for dirPath, dirNames, fileNames in os.walk(root):
    dirNames[:] = sorted(d for d in dirNames if d != '__pycache__')
    for fileName in sorted(fileNames):
      if not fileName.endswith('.py'):
        continue
      absolute = os.path.join(dirPath, fileName)
      relative = os.path.relpath(absolute, root).replace(os.sep, '/')
      out.append(relative)
  return out


def collectPackage(package: str) -> list:
  root = packageDir(package)
  out = []
  for file in packageFiles(package):
    text = readText(os.path.join(root, file))
    _, mutants = collectFile(text, file)
    out.extend(mutants)
  return out


def packageHashes(package: str) -> dict:
  """The SHA-256 of every file of the package, keyed like the mutant
  table, so a resumed run can tell whether the source it was built on
  is still the source on disk."""
  root = packageDir(package)
  out = dict()
  for file in packageFiles(package):
    text = readText(os.path.join(root, file))
    out[file] = hashlib.sha256(text.encode('utf-8')).hexdigest()
  return out


def mutateSource(text: str, mutantId: str, file: str) -> str:
  """The source of 'file' with the mutant of 'mutantId' applied."""
  tree, mutants = collectFile(text, file)
  for mutant in mutants:
    if mutant.id == mutantId:
      mutant.apply()
      break
  else:
    raise RuntimeError('No mutant %s in the current source' % mutantId)
  ast.fix_missing_locations(tree)
  return ast.unparse(tree) + '\n'


# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#  RUNS   # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

class Run:
  """
  The files of one run, named by package and start time.
  """

  def __init__(self, package: str, stamp: str) -> None:
    self.package = package
    self.stamp = stamp
    self.prefix = os.path.join(RUNS, '%s_%s' % (package, stamp))

  def path(self, kind: str) -> str:
    return '%s_%s' % (self.prefix, kind)

  @property
  def meta(self) -> dict:
    return readJson(self.path('meta.json'), dict())

  @property
  def results(self) -> list:
    return readJsonLines(self.path('results.jsonl'))

  @property
  def attempts(self) -> list:
    return readJsonLines(self.path('attempts.jsonl'))

  @property
  def ids(self) -> list:
    return [m['id'] for m in self.meta['mutants']]

  def latest(self) -> dict:
    """The latest result line of every mutant that has one."""
    out = dict()
    for line in self.results:
      out[line['id']] = line
    return out


def listRuns(package: str) -> list:
  head = '%s_' % package
  tail = '_meta.json'
  out = []
  if not os.path.isdir(RUNS):
    return out
  for name in sorted(os.listdir(RUNS)):
    if name.startswith(head) and name.endswith(tail):
      stamp = name[len(head):-len(tail)]
      if '_' in stamp or '.' in stamp:
        continue
      out.append(Run(package, stamp))
  return out


def latestRun(package: str) -> Run:
  runs = listRuns(package)
  if not runs:
    raise SystemExit('No run of %s found under %s' % (package, RUNS))
  return runs[-1]


def unfinishedRun(package: str) -> object:
  """The latest run whose sweep has a mutant without a result, or None."""
  runs = listRuns(package)
  if not runs:
    return None
  run = runs[-1]
  recorded = set()
  for line in run.results:
    if line['pass'] == 0:
      recorded.add(line['id'])
  for mutantId in run.ids:
    if mutantId not in recorded:
      return run
  return None


def newStamp() -> str:
  return datetime.now().strftime('%Y%m%d-%H%M%S')


def writeRun(run: Run, mutants: list) -> None:
  """Writes the first files of a new run: the mutant table and the two
  empty logs."""
  package = run.package
  stamp = run.stamp
  meta = dict(
      package=package,
      stamp=stamp,
      python=PYTHON,
      created=datetime.now().isoformat(timespec='seconds'),
      total=len(mutants),
      files=packageHashes(package),
      mutants=[m.record() for m in mutants],
  )
  os.makedirs(RUNS, exist_ok=True)
  writeText(run.path('meta.json'), json.dumps(meta, indent=1))
  appendLine(run.path('results.jsonl'), '')
  appendLine(run.path('attempts.jsonl'), '')


def changedFiles(run: Run) -> list:
  """The files of the package whose source differs from the one the
  run was built on, in a fixed order."""
  recorded = run.meta['files']
  current = packageHashes(run.package)
  out = []
  for file in sorted(set(recorded) | set(current)):
    if file not in recorded or file not in current:
      out.append(file)
    elif recorded[file] != current[file]:
      out.append(file)
  return out


# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#  TESTS  # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

def prepareSandbox(sandbox: str, index: int) -> str:
  """A fresh copy of 'src', 'tests' and 'pyproject.toml' for worker
  'index' under 'sandbox', and an empty runtime directory for the shells
  its tests start; returns the path of the copy of 'src'."""
  box = os.path.join(sandbox, str(index))
  if os.path.exists(box):
    shutil.rmtree(box)
  os.makedirs(box)
  boxSrc = os.path.join(box, 'src')
  shutil.copytree(SRC, boxSrc, ignore=IGNORE_CACHE)
  for dirPath, _, _ in os.walk(boxSrc):
    #  A file where the bytecode folder would go: no bytecode of 'src'.
    writeText(os.path.join(dirPath, '__pycache__'), '')
  shutil.copytree(TESTS, os.path.join(box, 'tests'), ignore=IGNORE_CACHE)
  shutil.copy(PYPROJECT, os.path.join(box, 'pyproject.toml'))
  os.makedirs(os.path.join(box, 'run'), mode=0o700)
  writeText(os.path.join(box, '%s.py' % ORDER_MODULE), ORDER_PLUGIN)
  return boxSrc


def availableMemory() -> int:
  """The kibibytes the machine could still hand out, as its kernel
  estimates; a great many where the kernel does not say."""
  try:
    for line in readText('/proc/meminfo').splitlines():
      if line.startswith('MemAvailable:'):
        return int(line.split()[1])
  except (OSError, ValueError, IndexError):
    pass
  return 1 << 40


class Brake:
  """
  The test processes now running, and the means to end them all at once
  should the machine run short of memory in spite of every limit.
  """

  SHORT = 'stopped: the machine ran short of memory'

  def __init__(self) -> None:
    self.running = set()
    self.stopped = set()
    self.lock = threading.Lock()
    self.thread = None

  def enter(self, process: subprocess.Popen) -> None:
    with self.lock:
      self.running.add(process)

  def leave(self, process: subprocess.Popen) -> bool:
    """True if the process was ended by the brake."""
    with self.lock:
      self.running.discard(process)
      if process in self.stopped:
        self.stopped.discard(process)
        return True
      return False

  def pull(self) -> int:
    """Ends every running test process; returns how many."""
    with self.lock:
      processes = list(self.running)
      self.stopped.update(processes)
    for process in processes:
      try:
        process.kill()
      except OSError:
        pass
    return len(processes)

  def watch(self) -> None:
    """Starts the thread that pulls the brake when less than half of
    'MEMORY_FLOOR' is available."""
    if self.thread is not None:
      return

    def loop() -> None:
      while True:
        if availableMemory() < MEMORY_FLOOR // 2:
          count = self.pull()
          if count:
            print('Less than %d MiB available: ended %d test processes.' % (
              MEMORY_FLOOR // 2048, count,
            ))
          time.sleep(5.0)
        time.sleep(0.25)

    self.thread = threading.Thread(target=loop, daemon=True)
    self.thread.start()


BRAKE = Brake()


def awaitMemory() -> None:
  """Waits while the machine is short of memory, so that a sweep never
  pushes it into swapping on top of whatever else it is doing."""
  told = False
  while availableMemory() < MEMORY_FLOOR:
    if not told:
      print('Less than %d MiB available: waiting.' % (MEMORY_FLOOR // 1024))
      told = True
    time.sleep(5.0)


def runTests(
    boxSrc: str,
    timeout: float,
    mark: str = '',
    first: object = (),
    paths: object = ('tests',),
    record: str = '',
    limits: str = '',
) -> dict:
  """
  Runs the suite in the sandbox, or the tests the marker expression
  'mark' selects, or only the test files in 'paths', the tests and test
  files in 'first' before the others, and returns the status, the exit
  code, the duration and, for a kill, the first failing test. With
  'record', what every test took is written to that file; with 'limits',
  the file 'writeLimits' made, a test that takes far longer than it
  should ends the run, which is a kill by that test.
  """
  box = os.path.dirname(boxSrc)
  slowPath = os.path.join(box, SLOW_FILE)
  if os.path.exists(slowPath):
    os.remove(slowPath)
  command = [
    'sh', '-c', 'ulimit -v %d && exec "$@"' % MEMORY_LIMIT, 'sh',
    PYTHON, '-m', 'pytest', *paths, '-x', '-q', '--no-header',
    '-p', 'no:cacheprovider', '-o', 'addopts=', '-o', 'pythonpath=%s' % boxSrc,
    '--basetemp=%s' % os.path.join(box, 'tmp'),
    '-o', 'faulthandler_timeout=%d' % TEST_TIMEOUT,
    '-o', 'faulthandler_exit_on_timeout=true',
    '-p', ORDER_MODULE,
  ]
  if mark:
    command.extend(['-m', mark])
  env = dict(os.environ)
  #  Bytecode is wanted here, for the tests; see 'prepareSandbox'.
  env.pop('PYTHONDONTWRITEBYTECODE', None)
  env['XDG_RUNTIME_DIR'] = os.path.join(box, 'run')
  env['PYTHONPATH'] = box
  env[FIRST_KEY] = '\n'.join(first)
  env[TIMES_KEY] = record
  env[LIMITS_KEY] = limits
  started = time.monotonic()
  process = subprocess.Popen(
      command, cwd=box, env=env, stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT, text=True,
  )
  BRAKE.enter(process)
  try:
    output, _ = process.communicate(timeout=timeout)
  except subprocess.TimeoutExpired:
    process.kill()
    process.communicate()
    BRAKE.leave(process)
    duration = time.monotonic() - started
    return dict(status=TIMEOUT, exit=None, duration=round(duration, 3))
  except BaseException:
    #  Ctrl-C, for one: the process must not outlive the runner.
    process.kill()
    process.communicate()
    BRAKE.leave(process)
    raise
  braked = BRAKE.leave(process)
  duration = time.monotonic() - started
  code = process.returncode
  out = dict(exit=code, duration=round(duration, 3))
  if braked:
    out['status'] = ERROR
    out['error'] = Brake.SHORT
  elif code in (0, 5):
    out['status'] = SURVIVED
  elif code in (1, 2, 3):
    out['status'] = KILLED
    out['killedBy'] = _firstFailure(output)
    named = out['killedBy'].startswith(('FAILED ', 'ERROR '))
    if not named and os.path.exists(slowPath):
      #  The run ended inside a test: that test was not over in time.
      out['killedBy'] = _slowTest(slowPath)
  else:
    out['status'] = ERROR
    out['error'] = output[-500:]
  return out


def _slowTest(path: str) -> str:
  """The line for a kill by a test that was ended for taking too long,
  from the file in which the plugin names the running test."""
  parts = readText(path).splitlines()
  test = parts[0] if parts else '?'
  limit = float(parts[1]) if len(parts) > 1 and float(parts[1]) else None
  normal = float(parts[2]) if len(parts) > 2 else 0.0
  if limit is None:
    return 'SLOW %s - not over after %ds' % (test, TEST_TIMEOUT)
  return 'SLOW %s - not over after %.1fs, takes %.2fs untouched' % (
    test, limit, normal,
  )


def writeLimits(timesPath: str, limitsPath: str) -> int:
  """From what every test took on the untouched source, writes what
  every test may take; returns how many tests got a limit below
  'TEST_TIMEOUT'."""
  times = readJson(timesPath, dict())
  limits = dict()
  for test, normal in times.items():
    limit = SLOW_SLACK + SLOW_FACTOR * normal
    if limit < TEST_TIMEOUT:
      limits[test] = [round(limit, 2), round(normal, 3)]
  writeText(limitsPath, json.dumps(limits))
  return len(limits)


def timeLimits(boxSrc: str, sandbox: str, mark: str, paths: object) -> tuple:
  """The baseline of a run: the tests on the untouched source, once to
  see that they pass and what each of them takes, and once more under
  the limits that follow from that, which they have to pass as well.
  Returns the result of the first and the file of the limits, or '' for
  the file when the second did not pass: then there are no limits."""
  timesPath = os.path.join(sandbox, 'times.json')
  limitsPath = os.path.join(sandbox, 'limits.json')
  baseline = runTests(boxSrc, 600.0, mark, (), paths, record=timesPath)
  if baseline['status'] != SURVIVED:
    return baseline, ''
  count = writeLimits(timesPath, limitsPath)
  proof = runTests(boxSrc, 600.0, mark, (), paths, limits=limitsPath)
  if proof['status'] != SURVIVED:
    print('The untouched source does not get through under the time '
          'limits (%s): none are used.' % proof.get('killedBy', '?'))
    return baseline, ''
  print('Time limits for %d tests: %g times what a test takes, and %gs.' % (
    count, SLOW_FACTOR, SLOW_SLACK,
  ))
  return baseline, limitsPath


def _firstFailure(output: str) -> str:
  for line in output.splitlines():
    if line.startswith('FAILED ') or line.startswith('ERROR '):
      return line[:200]
  for line in output.splitlines():
    if 'Error' in line:
      return line[:200]
  return ''


# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#  SWEEP  # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

def _killerTest(killedBy: str) -> str:
  """The test a 'FAILED' or 'ERROR' line of pytest names, or the 'SLOW'
  line of a test ended for taking too long, as pytest calls it
  ('tests/test_x.py::test_y[z]'), or ''."""
  for head in ('FAILED ', 'ERROR ', 'SLOW '):
    if killedBy.startswith(head):
      name = killedBy[len(head):].split(' - ')[0].strip()
      if name.split('::')[0].endswith('.py'):
        return name
  return ''


def _killerFile(killedBy: str) -> str:
  """The test file a 'FAILED' or 'ERROR' line of pytest names, or ''."""
  return _killerTest(killedBy).split('::')[0].split(' ')[0]


class Killers:
  """
  Which test killed the mutant of which line of each source file, and
  how often each test file has killed a mutant of it, locked so workers
  may ask and tell from their threads.
  """

  def __init__(self, lines: object = ()) -> None:
    self.counts = dict()
    self.places = dict()
    self.lock = threading.Lock()
    for line in lines:
      self.learn(line)

  def learn(self, line: dict) -> None:
    if line.get('status') != KILLED:
      return
    test = _killerTest(line.get('killedBy', ''))
    testFile = test.split('::')[0].split(' ')[0]
    if not testFile:
      return
    with self.lock:
      file = line.get('file', '')
      counts = self.counts.setdefault(file, dict())
      counts[testFile] = counts.get(testFile, 0) + 1
      if '::' in test:
        bisect.insort(
            self.places.setdefault(file, []), (line.get('line', 0), test),
        )

  def first(self, file: str, line: int = 0) -> list:
    """What to run first for the mutant of 'line' in 'file': the tests
    that killed the mutants nearest to it, nearest first; then the test
    files that killed the most of the mutants around it, since a test
    that has not killed anything yet is most likely one of theirs; then
    the test files that have killed the most mutants of 'file'."""
    with self.lock:
      counts = dict(self.counts.get(file, dict()))
      places = self.places.get(file, [])
      tests = []
      around = dict()
      above = bisect.bisect_left(places, (line, ''))
      below = above - 1
      for scanned in range(FIRST_SCAN):
        up = places[above] if above < len(places) else None
        down = places[below] if below >= 0 else None
        if up is None and down is None:
          break
        if down is None or (up is not None
                            and up[0] - line <= line - down[0]):
          found = up[1]
          above += 1
        else:
          found = down[1]
          below -= 1
        if (scanned < FIRST_NEAR and len(tests) < FIRST_TESTS
            and found not in tests):
          tests.append(found)
        testFile = found.split('::')[0]
        around[testFile] = around.get(testFile, 0) + 1
    near = sorted(around, key=lambda name: (-around[name], name))
    names = near[:FIRST_FILES]
    for name in sorted(counts, key=lambda name: (-counts[name], name)):
      if len(names) >= 2 * FIRST_FILES:
        break
      if name not in names:
        names.append(name)
    return tests + names


class Recorder:
  """
  The one writer of the attempts, the results and the progress line of
  a run, locked so workers may report from their threads.
  """

  def __init__(
      self,
      run: Run,
      passNo: int,
      total: int,
      done: int,
      counts: dict,
      timeout: float,
      mark: str = '',
  ) -> None:
    self.run = run
    self.passNo = passNo
    self.total = total
    self.done = done
    self.counts = counts
    self.timeout = timeout
    self.mark = mark
    #  The file of what every test may take, or '' for no limits.
    self.limits = ''
    self.killers = Killers(run.results)
    self.started = time.monotonic()
    self.running = []
    self.lock = threading.Lock()
    self.progress()

  def attempt(self, mutant: Mutant) -> None:
    line = dict(
        id=mutant.id,
        at=datetime.now().isoformat(timespec='seconds'),
    )
    line['pass'] = self.passNo
    with self.lock:
      appendLine(self.run.path('attempts.jsonl'), json.dumps(line))
      self.running.append(mutant.id)
      self.progress()

  def result(self, line: dict) -> None:
    line['pass'] = self.passNo
    line['at'] = datetime.now().isoformat(timespec='seconds')
    if self.mark:
      line['mark'] = self.mark
    self.killers.learn(line)
    with self.lock:
      appendLine(self.run.path('results.jsonl'), json.dumps(line))
      if line['id'] in self.running:
        self.running.remove(line['id'])
      self.done += 1
      self.counts[line['status']] += 1
      self.progress()

  def progress(self) -> None:
    elapsed = int(time.monotonic() - self.started)
    clock = '%02d:%02d:%02d' % (
      elapsed // 3600, elapsed % 3600 // 60, elapsed % 60,
    )
    running = ','.join(self.running) if self.running else '-'
    line = (
        '%d/%d killed=%d survived=%d timeouts=%d errors=%d crashed=%d '
        'elapsed=%s pass=%d mark=%s timeout=%ds pid=%d running=%s'
    ) % (
      self.done, self.total, self.counts[KILLED], self.counts[SURVIVED],
      self.counts[TIMEOUT], self.counts[ERROR], self.counts[CRASHED],
      clock, self.passNo, self.mark or '-', int(self.timeout), os.getpid(),
      running,
    )
    writeText(self.run.path('progress.txt'), line + '\n')


def _tryMutant(
    mutant: Mutant,
    package: str,
    boxSrc: str,
    original: str,
    timeout: float,
    mark: str,
    first: list,
    paths: object = ('tests',),
    limits: str = '',
) -> dict:
  """Writes the mutant into the sandbox, runs the tests against it and
  puts the original back; returns the record of the mutant with the
  result, an error while doing so included."""
  line = mutant.record()
  try:
    mutated = mutateSource(original, mutant.id, mutant.file)
    target = os.path.join(boxSrc, *package.split('.'), mutant.file)
    writeText(target, mutated)
    try:
      line.update(runTests(
          boxSrc, timeout, mark, first, paths, limits=limits,
      ))
    finally:
      writeText(target, original)
  except Exception as exception:
    line['status'] = ERROR
    line['exit'] = None
    line['duration'] = 0.0
    line['error'] = '%s: %s' % (type(exception).__name__, exception)
  return line


def _work(
    mutant: Mutant,
    package: str,
    boxes: queue.Queue,
    originals: dict,
    timeout: float,
    recorder: Recorder,
) -> dict:
  boxSrc = boxes.get()
  try:
    awaitMemory()
    recorder.attempt(mutant)
    return _tryMutant(
        mutant, package, boxSrc, originals[mutant.file], timeout,
        recorder.mark, recorder.killers.first(mutant.file, mutant.line),
        limits=recorder.limits,
    )
  finally:
    boxes.put(boxSrc)


def _executeBatch(
    mutants: list,
    package: str,
    boxes: queue.Queue,
    jobs: int,
    originals: dict,
    timeout: float,
    recorder: Recorder,
) -> None:
  if not mutants:
    return
  pool = ThreadPoolExecutor(max_workers=jobs)
  futures = []
  for mutant in mutants:
    future = pool.submit(
        _work, mutant, package, boxes, originals, timeout, recorder,
    )
    futures.append(future)
  try:
    for future in as_completed(futures):
      recorder.result(future.result())
  except KeyboardInterrupt:
    #  Ctrl-C reaches the running pytests too, which exit at once with
    #  code 2, a kill that is not the mutant's: their lines are dropped,
    #  so the resume reruns them alone, and the queued mutants never
    #  start.
    pool.shutdown(wait=True, cancel_futures=True)
    raise
  pool.shutdown(wait=True)


def _selectTargets(run: Run, passNo: int, only: list) -> list:
  """The IDs this pass runs, in the order of the mutant table: every
  mutant for the sweep, the current survivors for a rerun, or those
  given."""
  ids = run.ids
  if only:
    chosen = set(only)
    unknown = chosen - set(ids)
    if unknown:
      raise SystemExit('Unknown mutant IDs: %s' % ', '.join(sorted(unknown)))
    return [i for i in ids if i in chosen]
  if passNo == 0:
    return ids
  latest = run.latest()
  out = []
  for mutantId in ids:
    if mutantId in latest and latest[mutantId]['status'] == SURVIVED:
      out.append(mutantId)
  return out


def _baselineRefusal(baseline: dict, package: str) -> str:
  """The reason a run must not start, or '' when the baseline is sound:
  a suite that fails on the pristine source measures nothing, and so
  does one in which pytest collects no tests, which is exit code 5."""
  if baseline['exit'] == 5:
    return (
      'pytest collected no tests under tests/, so a sweep would mark\n'
      'every mutant survived and measure nothing. The map of a suite\n'
      'that does not exist yet is the listing, not a sweep:\n'
      '  mutate.py list --package %s --verbose\n'
      'Write the first tests from it, then sweep.' % package
    )
  if baseline['status'] != SURVIVED:
    return 'The tests fail on the pristine source; nothing to measure.\n%s' % (
      baseline.get('killedBy', baseline.get('error', '')),
    )
  return ''


def commandRun(args: argparse.Namespace) -> int:
  package = args.package
  mutants = collectPackage(package)
  byId = {m.id: m for m in mutants}
  only = [i for i in args.ids.split(',') if i] if args.ids else []
  created = False
  if args.passNo == 0 and not args.fresh:
    run = unfinishedRun(package)
    if run is None:
      run = Run(package, newStamp())
      created = True
  elif args.passNo == 0:
    run = Run(package, newStamp())
    created = True
  else:
    run = latestRun(package)
  if not created:
    changed = changedFiles(run)
    if changed:
      print('The source of %s changed since run %s was built:' % (
        package, run.stamp,
      ))
      for file in changed:
        print('  %s' % file)
      print('An ID is a position, so the mutants of that run are no '
            'longer the ones it recorded. Sweep the current source '
            'with --fresh.')
      return 2
  sandbox = run.path('sandbox')
  timeout = args.timeout
  boxes = queue.Queue()
  jobs = max(1, args.jobs)
  boxSrcs = [prepareSandbox(sandbox, k) for k in range(jobs)]
  for boxSrc in boxSrcs:
    boxes.put(boxSrc)
  baseline, limits = timeLimits(boxSrcs[0], sandbox, args.mark, ('tests',))
  print('Baseline: %s in %.1fs (exit %s)' % (
    baseline['status'], baseline['duration'], baseline['exit'],
  ))
  refusal = _baselineRefusal(baseline, package)
  if refusal:
    print(refusal)
    if not args.keepSandbox and os.path.exists(sandbox):
      shutil.rmtree(sandbox)
    return 2
  if timeout is None:
    timeout = max(20.0, baseline['duration'] * 10.0 + 5.0)
  if created:
    writeRun(run, mutants)
    print('New run %s with %d mutants' % (run.prefix, len(mutants)))
  elif args.passNo == 0:
    print('Resuming run %s' % run.prefix)
  else:
    print('Rerun pass %d on run %s' % (args.passNo, run.prefix))
  targets = _selectTargets(run, args.passNo, only)
  recorded = dict()
  for line in run.results:
    if line['pass'] == args.passNo:
      recorded[line['id']] = line
  attempts = dict()
  for line in run.attempts:
    if line['pass'] == args.passNo:
      attempts[line['id']] = attempts.get(line['id'], 0) + 1
  counts = {status: 0 for status in STATUSES}
  for mutantId in targets:
    if mutantId in recorded:
      counts[recorded[mutantId]['status']] += 1
  pending = [i for i in targets if i not in recorded]
  crashed = [i for i in pending if attempts.get(i, 0) >= 2]
  retried = [i for i in pending if attempts.get(i, 0) == 1]
  fresh = [i for i in pending if attempts.get(i, 0) == 0]
  recorder = Recorder(
      run, args.passNo, len(targets), len(recorded), counts, timeout,
      args.mark,
  )
  recorder.limits = limits
  for mutantId in crashed:
    line = byId[mutantId].record()
    line['status'] = CRASHED
    line['exit'] = None
    line['duration'] = 0.0
    line['error'] = 'started twice without a result'
    recorder.result(line)
  print('Pass %d: %d targets, %d recorded, %d crashed, %d retried, '
        '%d fresh, timeout %ds, jobs %d, tests: %s' % (
          args.passNo, len(targets), len(recorded), len(crashed),
          len(retried), len(fresh), timeout, jobs,
          args.mark or 'all',
        ))
  originals = dict()
  root = packageDir(package)
  for file in packageFiles(package):
    originals[file] = readText(os.path.join(root, file))
  retriedMutants = [byId[i] for i in retried]
  freshMutants = [byId[i] for i in fresh]
  interrupted = False
  try:
    _executeBatch(
        retriedMutants, package, boxes, 1, originals, timeout, recorder,
    )
    _executeBatch(
        freshMutants, package, boxes, jobs, originals, timeout, recorder,
    )
  except KeyboardInterrupt:
    interrupted = True
  if not args.keepSandbox and os.path.exists(sandbox):
    shutil.rmtree(sandbox)
  writeReport(run)
  progress = readText(run.path('progress.txt')).strip()
  if interrupted:
    print('Interrupted: %s' % progress)
    print('The same command resumes the run.')
    return 130
  print('Done: %s' % progress)
  return 0


# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#  REPORT   # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

def _sourceLine(package: str, file: str, line: int) -> str:
  path = os.path.join(packageDir(package), file)
  if not os.path.exists(path):
    return ''
  lines = readText(path).splitlines()
  if 0 < line <= len(lines):
    return lines[line - 1].strip()
  return ''


def writeReport(run: Run) -> str:
  """
  Writes 'survivors.md': the totals, every survivor grouped by file
  with its verdict from 'verdicts.json', the mutants killed in rerun
  passes with the test that killed them, and 'notes.md' if present.
  """
  meta = run.meta
  package = meta['package']
  latest = run.latest()
  verdicts = readJson(run.path('verdicts.json'), dict())
  counts = {status: 0 for status in STATUSES}
  pendingCount = 0
  for record in meta['mutants']:
    if record['id'] in latest:
      counts[latest[record['id']]['status']] += 1
    else:
      pendingCount += 1
  survivors = [
    r for r in meta['mutants']
    if r['id'] in latest and latest[r['id']]['status'] == SURVIVED
  ]
  needTest = 0
  equivalent = 0
  unclassified = 0
  for record in survivors:
    if record['id'] not in verdicts:
      unclassified += 1
    elif verdicts[record['id']]['verdict'] == 'equivalent':
      equivalent += 1
    else:
      needTest += 1
  lines = []
  lines.append('# Survivors of %s, run %s' % (package, run.stamp))
  lines.append('')
  lines.append(
      'Latest result per mutant: %d mutants, %d killed, %d survived, '
      '%d timed out, %d errors, %d crashed, %d not yet run.' % (
        len(meta['mutants']), counts[KILLED], counts[SURVIVED],
        counts[TIMEOUT], counts[ERROR], counts[CRASHED], pendingCount,
      )
  )
  lines.append('')
  lines.append(
      'Survivors: %d need a test, %d equivalent, %d unclassified.' % (
        needTest, equivalent, unclassified,
      )
  )
  lines.append('')
  byFile = dict()
  for record in survivors:
    byFile.setdefault(record['file'], []).append(record)
  for file in sorted(byFile):
    lines.append('## %s' % file)
    lines.append('')
    for record in byFile[file]:
      mutantId = record['id']
      if mutantId in verdicts:
        verdict = verdicts[mutantId]
        if verdict['verdict'] == 'equivalent':
          mark = 'equivalent: %s' % verdict['reason']
          if verdict.get('review', False):
            mark = '%s (carried over from `%s` into a function that has '\
                   'changed since: to be read again)' % (
                     mark, verdict.get('was', '?'),
                   )
        elif verdict.get('reason', ''):
          mark = 'needs a test: %s' % verdict['reason']
        else:
          mark = 'needs a test'
      else:
        mark = 'unclassified'
      source = _sourceLine(package, file, record['line'])
      lines.append('- `%s` line %d: %s' % (
        mutantId, record['line'], record['description'],
      ))
      if source:
        lines.append('  `%s`' % source)
      lines.append('  %s' % mark)
    lines.append('')
  rerunKilled = [
    r for r in meta['mutants']
    if r['id'] in latest
    and latest[r['id']]['status'] == KILLED
    and latest[r['id']]['pass'] > 0
  ]
  if rerunKilled:
    lines.append('## Killed in rerun passes')
    lines.append('')
    for record in rerunKilled:
      line = latest[record['id']]
      lines.append('- `%s` (pass %d): %s' % (
        record['id'], line['pass'], line.get('killedBy', ''),
      ))
    lines.append('')
  others = [
    r for r in meta['mutants']
    if r['id'] in latest
    and latest[r['id']]['status'] in (TIMEOUT, ERROR, CRASHED)
  ]
  if others:
    lines.append('## Timeouts, errors and crashes')
    lines.append('')
    for record in others:
      line = latest[record['id']]
      lines.append('- `%s`: %s %s' % (
        record['id'], line['status'], line.get('error', ''),
      ))
    lines.append('')
  notesPath = run.path('notes.md')
  if os.path.exists(notesPath):
    lines.append('## Notes')
    lines.append('')
    lines.append(readText(notesPath).rstrip())
    lines.append('')
  text = '\n'.join(lines)
  writeText(run.path('survivors.md'), text)
  return text


def commandReport(args: argparse.Namespace) -> int:
  run = latestRun(args.package)
  text = writeReport(run)
  print(text.splitlines()[2])
  print(text.splitlines()[4])
  print('Written: %s' % run.path('survivors.md'))
  return 0


# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#  CARRY  # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

_ID_IN_TEXT = re.compile(r'\b\d+:\d+:[a-z]+:\d+\b')


def _renamed(reason: str, file: str, names: dict) -> str:
  """'reason' with the mutants it mentions, by the part of their ID
  after the file, called what they are called now."""

  def rename(match: re.Match) -> str:
    new = names.get('%s:%s' % (file, match.group(0)))
    if new is None:
      return match.group(0)
    return new.split(':', 1)[1]

  return _ID_IN_TEXT.sub(rename, reason)


def carryVerdicts(older: Run, newer: Run) -> tuple:
  """Gives the survivors of 'newer' the verdicts 'older' has for the
  mutants of the same key. Returns how many it carried, how many of
  those went into a function that has changed, the old IDs whose
  mutant is killed now, and the old IDs whose mutant has no
  counterpart."""
  oldRecords = {r['id']: r for r in older.meta['mutants']}
  newRecords = {tuple(r['key']): r for r in newer.meta['mutants']}
  names = dict()
  for oldId, record in oldRecords.items():
    twin = newRecords.get(tuple(record.get('key', ())))
    if twin is not None:
      names[oldId] = twin['id']
  latest = newer.latest()
  verdicts = readJson(newer.path('verdicts.json'), dict())
  carried = 0
  changed = 0
  killed = []
  lost = []
  for oldId, verdict in readJson(older.path('verdicts.json'), dict()).items():
    newId = names.get(oldId)
    if newId is None:
      lost.append(oldId)
      continue
    if newId in latest and latest[newId]['status'] != SURVIVED:
      killed.append(oldId)
      continue
    if newId in verdicts:
      continue
    entry = dict(verdict, was=oldId)
    file = oldRecords[oldId]['file']
    if 'reason' in entry:
      entry['reason'] = _renamed(entry['reason'], file, names)
    twin = newRecords[tuple(oldRecords[oldId]['key'])]
    if twin['around'] != oldRecords[oldId]['around']:
      entry['review'] = True
      changed += 1
    else:
      entry.pop('review', None)
    verdicts[newId] = entry
    carried += 1
  writeText(newer.path('verdicts.json'),
            json.dumps(verdicts, indent=1, ensure_ascii=False))
  return carried, changed, killed, lost


def commandCarry(args: argparse.Namespace) -> int:
  package = args.package
  newer = latestRun(package)
  if args.source:
    older = Run(package, args.source)
  else:
    earlier = [
      run for run in listRuns(package)
      if run.stamp != newer.stamp
      and os.path.exists(run.path('verdicts.json'))
    ]
    if not earlier:
      raise SystemExit('No earlier run of %s has a verdicts.json' % package)
    older = earlier[-1]
  records = older.meta.get('mutants', ())
  if not records or 'key' not in records[0]:
    raise SystemExit(
        'Run %s was made before mutants had keys: nothing tells which '
        'mutant of today one of its IDs was.' % older.stamp
    )
  carried, changed, killed, lost = carryVerdicts(older, newer)
  print('From run %s to run %s: %d verdicts carried over.' % (
    older.stamp, newer.stamp, carried,
  ))
  if changed:
    print(
        '%d of them went into a function that has changed and are '
        'marked "review": read the reason against the code as it is, '
        'then take the mark out.' % changed
    )
  if killed:
    print('%d are not needed any more, their mutants are killed.' % (
      len(killed),
    ))
  if lost:
    print('%d have no mutant to go to, their code has changed:' % len(lost))
    for oldId in lost:
      print('  %s' % oldId)
  return 0


# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#  LIST, SHOW, CHECK  # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

def commandList(args: argparse.Namespace) -> int:
  package = args.package
  root = packageDir(package)
  if not os.path.isdir(root):
    raise SystemExit('No package directory at %s' % root)
  byFile = dict()
  byKind = dict()
  total = 0
  for file in packageFiles(package):
    text = readText(os.path.join(root, file))
    tree, mutants = collectFile(text, file)
    again = ast.parse(ast.unparse(tree))
    if ast.dump(again) != ast.dump(ast.parse(text)):
      print('WARNING: %s does not round-trip through ast.unparse' % file)
    byFile[file] = len(mutants)
    total += len(mutants)
    for mutant in mutants:
      byKind[mutant.kind] = byKind.get(mutant.kind, 0) + 1
      if args.verbose:
        print('%s  %s' % (mutant.id, mutant.description))
  for file in sorted(byFile):
    print('%5d  %s' % (byFile[file], file))
  print('%5d  total' % total)
  for kind in sorted(byKind):
    print('%5d  %s' % (byKind[kind], kind))
  return 0


def writeListing(run: Run) -> str:
  """
  Writes 'mutants.txt': every source line of the package that has a
  mutant, followed by the kind, variant and description of each mutant
  on it, grouped by file, so the map of the run can be read beside the
  code.
  """
  meta = run.meta
  package = meta['package']
  root = packageDir(package)
  byFile = dict()
  for record in meta['mutants']:
    byFile.setdefault(record['file'], []).append(record)
  lines = []
  for file in packageFiles(package):
    if file not in byFile:
      continue
    source = readText(os.path.join(root, file)).splitlines()
    lines.append('=' * 70)
    lines.append('%s  (%d mutants)' % (file, len(byFile[file])))
    byLine = dict()
    for record in byFile[file]:
      byLine.setdefault(record['line'], []).append(record)
    for line in sorted(byLine):
      if 0 < line <= len(source):
        text = source[line - 1].rstrip()
      else:
        text = ''
      lines.append('%4d | %s' % (line, text))
      for record in byLine[line]:
        lines.append('       %s:%d  %s' % (
          record['kind'], record['variant'], record['description'],
        ))
  text = '\n'.join(lines) + '\n'
  writeText(run.path('mutants.txt'), text)
  return text


def commandListing(args: argparse.Namespace) -> int:
  run = latestRun(args.package)
  text = writeListing(run)
  print('Written: %s (%d lines)' % (
    run.path('mutants.txt'), len(text.splitlines()),
  ))
  return 0


def _findMutant(package: str, mutantId: str) -> Mutant:
  for mutant in collectPackage(package):
    if mutant.id == mutantId:
      return mutant
  raise SystemExit('No mutant %s in %s' % (mutantId, package))


def commandShow(args: argparse.Namespace) -> int:
  mutant = _findMutant(args.package, args.id)
  path = os.path.join(packageDir(args.package), mutant.file)
  text = readText(path)
  before = ast.unparse(ast.parse(text)) + '\n'
  after = mutateSource(text, mutant.id, mutant.file)
  diff = difflib.unified_diff(
      before.splitlines(), after.splitlines(),
      'original', mutant.id, lineterm='', n=2,
  )
  print(mutant.id, mutant.description)
  print('\n'.join(diff))
  return 0


def commandCheck(args: argparse.Namespace) -> int:
  """Runs the baseline and the mutants given, in a sandbox of this
  process alone, writing no run: several checks may run side by side,
  and beside a run."""
  package = args.package
  byId = {m.id: m for m in collectPackage(package)}
  ids = [i for i in args.id.split(',') if i]
  unknown = [i for i in ids if i not in byId]
  if unknown:
    raise SystemExit('No mutant %s in %s' % (', '.join(unknown), package))
  killers = Killers()
  if listRuns(package):
    killers = Killers(latestRun(package).results)
  name = '%s_check_%d_sandbox' % (package, os.getpid())
  sandbox = os.path.join(RUNS, name)
  jobs = max(1, min(args.jobs, len(ids)))
  boxes = queue.Queue()
  survived = 0
  try:
    boxSrcs = [prepareSandbox(sandbox, k) for k in range(jobs)]
    for boxSrc in boxSrcs:
      boxes.put(boxSrc)
    paths = tuple(args.tests.split(',')) if args.tests else ('tests',)
    baseline, limits = timeLimits(boxSrcs[0], sandbox, args.mark, paths)
    print('baseline: %s' % baseline)
    if baseline['status'] != SURVIVED:
      print('The tests fail on the pristine source; nothing to check.')
      return 2
    timeout = max(20.0, baseline['duration'] * 10.0 + 5.0)

    def work(mutantId: str) -> dict:
      mutant = byId[mutantId]
      path = os.path.join(packageDir(package), mutant.file)
      boxSrc = boxes.get()
      try:
        awaitMemory()
        return _tryMutant(
            mutant, package, boxSrc, readText(path), timeout, args.mark,
            killers.first(mutant.file, mutant.line), paths, limits,
        )
      finally:
        boxes.put(boxSrc)

    pool = ThreadPoolExecutor(max_workers=jobs)
    try:
      for line in pool.map(work, ids):
        if line['status'] == SURVIVED:
          survived += 1
        detail = line.get('killedBy', line.get('error', ''))
        print('%s: %s (%.1fs) %s' % (
          line['id'], line['status'], line['duration'], detail,
        ))
    finally:
      pool.shutdown(wait=True, cancel_futures=True)
  finally:
    if os.path.exists(sandbox):
      shutil.rmtree(sandbox)
  print('%d checked, %d survived' % (len(ids), survived))
  return 0


# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#  MAIN   # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

def _addLimits(parser: argparse.ArgumentParser) -> None:
  parser.add_argument('--memory', type=int, default=MEMORY_LIMIT // 1024,
                      help='the MiB one test process may map; jobs times '
                           'this is the most a sweep can take')
  parser.add_argument('--test-timeout', dest='testTimeout', type=int,
                      default=TEST_TIMEOUT,
                      help='the seconds one test may take')


def main(argv: list) -> int:
  parser = argparse.ArgumentParser(
      description='Mutation testing of a jupyterminal package.',
      epilog=_describeKinds(),
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  commands = parser.add_subparsers(dest='command', required=True)

  listParser = commands.add_parser('list', help='count the mutants')
  listParser.add_argument('--package', required=True)
  listParser.add_argument('--verbose', action='store_true')
  listParser.set_defaults(func=commandList)

  showParser = commands.add_parser('show', help='diff one mutant')
  showParser.add_argument('--package', required=True)
  showParser.add_argument('--id', required=True)
  showParser.set_defaults(func=commandShow)

  checkParser = commands.add_parser('check', help='try one mutant')
  checkParser.add_argument('--package', required=True)
  checkParser.add_argument('--id', required=True,
                           help='one ID, or several separated by commas')
  checkParser.add_argument('--mark', default='')
  checkParser.add_argument('--jobs', type=int, default=4)
  checkParser.add_argument('--tests', default='',
                           help='only these test files, separated by '
                                'commas: whether new tests kill a mutant '
                                'is settled by those tests alone')
  _addLimits(checkParser)
  checkParser.set_defaults(func=commandCheck)

  runParser = commands.add_parser('run', help='sweep or rerun')
  runParser.add_argument('--package', required=True)
  runParser.add_argument('--pass', dest='passNo', type=int, default=0)
  runParser.add_argument('--jobs', type=int, default=4)
  runParser.add_argument('--timeout', type=float, default=None)
  runParser.add_argument('--ids', default='')
  runParser.add_argument('--fresh', action='store_true')
  runParser.add_argument('--mark', default='',
                         help='a pytest marker expression: the tests of '
                              'this pass')
  runParser.add_argument('--keep-sandbox', dest='keepSandbox',
                         action='store_true')
  _addLimits(runParser)
  runParser.set_defaults(func=commandRun)

  carryParser = commands.add_parser(
      'carry', help='take the verdicts of an earlier run along',
  )
  carryParser.add_argument('--package', required=True)
  carryParser.add_argument('--from', dest='source', default='',
                           help='the stamp of the run to take them from; '
                                'the latest earlier run with verdicts if '
                                'not given')
  carryParser.set_defaults(func=commandCarry)

  reportParser = commands.add_parser('report', help='write survivors.md')
  reportParser.add_argument('--package', required=True)
  reportParser.set_defaults(func=commandReport)

  listingParser = commands.add_parser('listing', help='write mutants.txt')
  listingParser.add_argument('--package', required=True)
  listingParser.set_defaults(func=commandListing)

  args = parser.parse_args(argv)
  if args.command in ('run', 'check'):
    global MEMORY_LIMIT, TEST_TIMEOUT
    MEMORY_LIMIT = max(256, args.memory) * 1024
    TEST_TIMEOUT = max(1, args.testTimeout)
    #  Background work: whoever is at the machine comes first.
    os.nice(19)
    BRAKE.watch()
  return args.func(args)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
