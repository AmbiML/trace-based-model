# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Collection of general purpose utilities."""

import enum
import itertools
import logging
import sys
import threading
import time
from typing import Any, Callable, Iterable, Sequence


def logging_config(level: int) -> None:
    """Configure the root logger to track events starting from `level`.

    If tracked, WARNING events (and above) go to stderr, other events go to
    stdout.
    """
    # Log messages below WARNING go to stdout.
    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setLevel(logging.DEBUG)
    stdout_h.addFilter(lambda record: record.levelno < logging.WARNING)

    # Log messages that are WARNING and above go to stderr.
    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setLevel(logging.WARNING)

    formatter = logging.Formatter("[%(asctime)s] %(message)s",
                                  datefmt="%H:%M:%S'")
    stdout_h.setFormatter(formatter)
    stderr_h.setFormatter(formatter)

    logging.basicConfig(handlers=[stdout_h, stderr_h],
                        level=level)


class FileFormat(enum.Enum):
    FLATBUFFERS = enum.auto()
    JSON = enum.auto()


def flatten(xss: Iterable[Iterable[Any]]) -> Sequence[Any]:
    return list(itertools.chain.from_iterable(xss))


class CallEvery():
    """Call some function every few seconds.

    `with CallEvery(s, f) as c: ...` calls `f()` every `s` seconds, while inside
    the with block.
    """

    def __init__(self, period, f: Callable[[], None]):
        self.period = period
        self.f = f
        self.running = False
        self.timer = None
        self.t = None

    def __enter__(self) -> None:
        self.running = True
        self.t = time.time() + self.period
        self.timer = threading.Timer(max(self.t - time.time(), 0), self._run)
        self.timer.start()

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.running = False
        if self.timer:
            self.timer.cancel()
        return False

    def _run(self):
        if self.running:
            self.f()
            self.t += self.period
            self.timer = threading.Timer(max(self.t - time.time(), 0),
                                         self._run)
            self.timer.start()
