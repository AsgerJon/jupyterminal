"""
TestTokenCount tests 'token_count.py'.
"""
#  Apache-2.0 license
#  Copyright (c) 2026 Asger Jon Vistisen
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLING = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLING not in sys.path:
  sys.path.insert(0, TOOLING)

import token_count  # noqa: E402


def usage(fresh: int, written: int, read: int, said: int) -> dict:
  """The usage object of a reply, as a transcript holds it."""
  return dict(input_tokens=fresh, cache_creation_input_tokens=written,
              cache_read_input_tokens=read, output_tokens=said,
              service_tier='standard')


def record(reply: str, request: str, stamp: str, used: dict) -> str:
  """One line of a transcript: one part of a reply."""
  return json.dumps(dict(
    type='assistant', requestId=request, timestamp=stamp,
    message=dict(id=reply, role='assistant', usage=used)))


class TestTokenCount(unittest.TestCase):
  """
  The badge of the README says how many tokens went into the program. A
  transcript repeats the usage of a reply in the record of every part of
  it, so a count that adds up the records says a multiple of that. Every
  test works on transcripts of its own, never on those of the machine.
  """

  def setUp(self) -> None:
    self.root = tempfile.mkdtemp(prefix='token-count-')
    self.addCleanup(shutil.rmtree, self.root, True)
    self.transcripts = os.path.join(self.root, 'transcripts')
    os.makedirs(os.path.join(self.transcripts, 'session', 'subagents'))

  def write(self, lines: list, *parts: str) -> None:
    path = os.path.join(self.transcripts, *parts)
    with open(path, 'w', encoding='utf-8') as handle:
      handle.write(str.join('\n', lines) + '\n')

  def test_a_reply_counts_once(self) -> None:
    self.write([
      #  One reply in three parts: the numbers of the last one are those
      #  of the whole reply.
      record('msg_a', 'req_1', '2026-01-01T10:00:00.000Z',
             usage(3, 100, 1000, 1)),
      record('msg_a', 'req_1', '2026-01-01T10:00:01.000Z',
             usage(3, 100, 1000, 1)),
      record('msg_a', 'req_1', '2026-01-01T10:00:02.000Z',
             usage(3, 100, 1000, 40)),
      #  What a person typed, and a line that is no record at all.
      json.dumps(dict(type='user', timestamp='2026-01-01T10:00:03.000Z',
                      message=dict(role='user', content='"usage" of it'))),
      '{"usage": broken',
      json.dumps(['"usage"']),
      record('msg_b', 'req_2', '2026-01-01T10:00:04.000Z',
             usage(1, 0, 2000, 7)),
    ], 'session.jsonl')
    #  Another file, of an agent the session started: its own replies,
    #  and one record of the file above over again.
    self.write([
      record('msg_c', 'req_3', '2026-01-01T10:00:05.000Z',
             usage(2, 10, 20, 5)),
      record('msg_a', 'req_1', '2026-01-01T10:00:02.000Z',
             usage(3, 100, 1000, 40)),
    ], 'session', 'subagents', 'agent.jsonl')
    self.write(['not a transcript'], 'notes.txt')
    self.assertEqual(token_count.countSince(self.transcripts),
                     (3 + 100 + 1000 + 40) + (1 + 2000 + 7)
                     + (2 + 10 + 20 + 5))

  def test_a_reply_belongs_to_the_time_it_began(self) -> None:
    self.write([
      record('msg_a', 'req_1', '2026-01-01T10:00:00.000Z',
             usage(0, 0, 0, 1)),
      #  The count is taken here, in the middle of the reply.
      record('msg_a', 'req_1', '2026-01-01T10:00:02.000Z',
             usage(0, 0, 0, 40)),
      record('msg_b', 'req_2', '2026-01-01T10:00:03.000Z',
             usage(0, 0, 0, 7)),
      #  The same name under another request is another reply.
      record('msg_b', 'req_9', '2026-01-01T10:00:04.000Z',
             usage(0, 0, 0, 500)),
    ], 'session.jsonl')
    cut = '2026-01-01T10:00:01.000Z'
    self.assertEqual(token_count.countSince(self.transcripts, cut), 507)
    self.assertEqual(token_count.countSince(self.transcripts), 547)
    self.assertEqual(token_count.countSince(
      self.transcripts, '2026-01-01T10:00:04.000Z'), 0)

  def test_records_that_say_less(self) -> None:
    self.write([
      #  Usage beside the message instead of in it, with no name: such a
      #  record stands for itself.  One of its numbers is missing.
      json.dumps(dict(timestamp='2026-01-01T10:00:00.000Z',
                      usage=dict(input_tokens=5, output_tokens=None))),
      json.dumps(dict(timestamp='2026-01-01T10:00:01.000Z',
                      usage=dict(input_tokens=6))),
      #  No time: counted where everything counts, and never as new.
      json.dumps(dict(message=dict(id='msg_x', usage=usage(1, 1, 1, 1)))),
      json.dumps(dict(message=dict(id='msg_y', usage='a lot'))),
    ], 'session.jsonl')
    self.assertEqual(token_count.countSince(self.transcripts), 5 + 6 + 4)
    self.assertEqual(token_count.countSince(
      self.transcripts, '2026-01-01T10:00:00.000Z'), 6)

  def test_the_ledger_grows_by_what_is_new(self) -> None:
    """Run as before a release: the first run counts everything, a later
    one what has come since, and the total adds up."""
    tooling = os.path.join(self.root, 'repo', 'bin', 'release_tooling')
    os.makedirs(tooling)
    for name in ('src', 'tests'):
      os.makedirs(os.path.join(self.root, 'repo', name))
    for name in ('pyproject.toml', 'README.md'):
      with open(os.path.join(self.root, 'repo', name), 'w') as handle:
        handle.write('\n')
    script = os.path.join(tooling, 'token_count.py')
    shutil.copy(os.path.join(TOOLING, 'token_count.py'), script)

    def count() -> dict:
      done = subprocess.run([sys.executable, '-B', script,
                             self.transcripts], capture_output=True,
                            text=True)
      self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
      with open(os.path.join(tooling, 'token_stats.json')) as handle:
        return json.load(handle)

    self.write([record('msg_a', 'req_1', '2026-01-01T10:00:00.000Z',
                       usage(1, 2, 3, 4)),
                record('msg_a', 'req_1', '2026-01-01T10:00:01.000Z',
                       usage(1, 2, 3, 4))], 'session.jsonl')
    stats = count()
    self.assertEqual(stats['cumulative'], 10)
    self.assertEqual([entry['periodTokens'] for entry in
                      stats['releases']], [10])
    #  A reply of a time yet to come: newer than the entry just made.
    self.write([record('msg_b', 'req_2', '2999-01-01T10:00:00.000Z',
                       usage(0, 0, 0, 5))], 'later.jsonl')
    stats = count()
    self.assertEqual(stats['cumulative'], 15)
    self.assertEqual([(entry['periodTokens'], entry['cumulative'])
                      for entry in stats['releases']], [(10, 10), (5, 15)])
