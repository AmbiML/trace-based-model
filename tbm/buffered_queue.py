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

"""Queue Module."""

import collections
import itertools
from typing import Optional, Sequence, TypeVar

import interfaces


# Declare type variable
T = TypeVar('T')


# pylint: disable-next=abstract-method
# __len__ and __iter__ are implemented by deque
class BufferedQueue(collections.deque[T], interfaces.ConsumableQueue[T]):
    """Queue model.

    For the owner this is a buffered queue; for the consumer this is
    ConsumableQueue. Elements that should not be visible from outside (e.g.
    during the computation of next state) can be added to the queue using
    `buffer(e)`. Call `flush` to make them visible.
    """

    def __init__(self, size: Optional[int]) -> None:
        """Construct a queue.

        Args:
          size: the size of the queue. `None` (or -1) for infinite queue.
        """
        super().__init__([])
        self._size = size if size != -1 else None
        self._buff = collections.deque()

    def is_buffer_full(self) -> bool:
        if self._size is not None:
            return len(self) + len(self._buff) >= self._size

        return False

    def buffer(self, item) -> None:
        self._buff.append(item)

    def flush(self) -> None:
        if self._size is None or len(self) + len(self._buff) <= self._size:
            self.extend(self._buff)
            self._buff.clear()
        else:
            for _ in range(self._size - len(self)):
                self.append(self._buff.popleft())

    def chain(self):
        return itertools.chain(self, self._buff)

    def pp_three_valued(self, vals: Sequence[str]) -> str:
        if self.is_buffer_full():
            # Full
            return vals[2]

        if any(self.chain()):
            # Partial
            return vals[1]

        # Empty
        return vals[0]

    # Implements interfaces.ConsumableQueue
    @property
    def size(self) -> Optional[int]:
        return self._size

    # Implements interfaces.ConsumableQueue
    def full(self) -> bool:
        return self._size is not None and len(self) >= self._size

    # Implements interfaces.ConsumableQueue
    def dequeue(self) -> Optional[T]:
        return self.popleft()

    # Implements interfaces.ConsumableQueue
    def peek(self) -> Optional[T]:
        return self[0] if self else None

    # Implements interfaces.ConsumableQueue
    # The following is useless, but without it pylint will issue an error
    # (E0110) for every instantiation of BufferedQueue.
    # pylint: disable-next=useless-super-delegation
    def __len__(self) -> int:
        return super().__len__()

    # Implements interfaces.ConsumableQueue
    # The following is useless, but without it pylint will issue an error
    # (E0110) for every instantiation of BufferedQueue.
    # pylint: disable-next=useless-super-delegation
    def __iter__(self):
        return super().__iter__()
