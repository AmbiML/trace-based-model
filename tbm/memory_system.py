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

"""MemorySystem module."""

import collections
import math
import re
import sys
from typing import Optional, Sequence, Union

from counter import Counter
import interfaces

class LRUSet:
    """LRU set."""

    def __init__(self, size: int) -> None:
        self.the_set = collections.deque(maxlen=size)
        self.dirty = set()

    def try_access(self, tag: int, set_dirty: bool) -> bool:
        try:
            self.the_set.remove(tag)
            self.the_set.append(tag)
            if set_dirty:
                self.dirty.add(tag)
            return True
        except ValueError:  # remove failed, hence tag not in set
            return False

    def evict(self) -> Optional[int]:
        if len(self.the_set) == self.the_set.maxlen:
            tag = self.the_set.popleft()
            try:
                self.dirty.remove(tag)
                return tag
            except KeyError:
                pass
        return None

    def insert(self, tag: int, dirty: bool) -> None:
        if self.the_set:
            self.dirty.discard(self.the_set[0])
        self.the_set.append(tag)
        if dirty:
            self.dirty.add(tag)

    def take(self, tag: int) -> bool:
        if self.the_set[-1] == tag:
            # This is an optimisation (take is always called after try_access)
            self.the_set.pop()
        else:
            self.the_set.remove(tag)
        try:
            self.dirty.remove(tag)
            return True
        except KeyError:
            return False


class DirectMapMem:
    """Direct Map."""

    def __init__(self, line_size_log2: int,
                 size_log2: int) -> None:

        self.line_size_log2 = line_size_log2
        self.index_size_log2 = size_log2 - self.line_size_log2
        self.tags = [None] * (2**self.index_size_log2)
        self.dirty = set()

    def index(self, addr: int) -> int:
        mask = (1 << self.index_size_log2) - 1
        return (addr >> self.line_size_log2) & mask

    def tag(self, addr: int) -> int:
        return addr >> (self.line_size_log2 + self.index_size_log2)

    def line_addr(self, addr: int) -> int:
        return (addr >> self.line_size_log2) << self.line_size_log2

    def try_access(self, addr: int, set_dirty: bool) -> bool:
        if self.tags[self.index(addr)] == self.tag(addr):
            if set_dirty:
                self.dirty.add(self.index(addr))
            return True

        return False

    def evict_for(self, addr: int) -> Optional[int]:
        i = self.index(addr)
        if self.tags[i] is not None:
            tag = self.tags[i]
            self.tags[i] = None
            try:
                self.dirty.remove(tag)
                return (
                    (tag << self.index_size_log2) | i) << self.line_size_log2
            except KeyError:
                pass
        return None

    def insert(self, addr: int, dirty: bool) -> None:
        self.tags[self.index(addr)] = self.tag(addr)
        if dirty:
            self.dirty.add(self.index(addr))
        else:
            self.dirty.discard(self.index(addr))

    def take(self, addr: int) -> bool:
        self.tags[self.index(addr)] = None
        try:
            self.dirty.remove(self.tag(addr))
            return True
        except KeyError:
            return False


class SetAssocMem:
    """Set associative."""

    def __init__(self, desc, line_size_log2: int, size_log2: int) -> None:
        self.line_size_log2 = line_size_log2

        # TODO(sflur): handle size that is not power of 2?
        set_size_log2 = int(math.log2(desc["set_size"]))
        self.index_size_log2 = size_log2 - self.line_size_log2 - set_size_log2

        if desc["replacement"] == "LRU":
            self.tags = [
                LRUSet(desc["set_size"]) for _ in range(2**self.index_size_log2)
            ]
        else:
            assert False

    def index(self, addr: int) -> int:
        mask = (1 << self.index_size_log2) - 1
        return (addr >> self.line_size_log2) & mask

    def tag(self, addr: int) -> int:
        return addr >> (self.line_size_log2 + self.index_size_log2)

    def line_addr(self, addr: int) -> int:
        return (addr >> self.line_size_log2) << self.line_size_log2

    def try_access(self, addr: int, set_dirty: bool) -> bool:
        return self.tags[self.index(addr)].try_access(self.tag(addr), set_dirty)

    def insert(self, addr: int, dirty: bool) -> None:
        self.tags[self.index(addr)].insert(self.tag(addr), dirty)

    def take(self, addr: int) -> bool:
        return self.tags[self.index(addr)].take(self.tag(addr))

    def evict_for(self, addr: int) -> Optional[int]:
        i = self.index(addr)
        tag = self.tags[i].evict()
        return (((tag << self.index_size_log2) | i) << self.line_size_log2
                if tag is not None else None)


def load_mem(desc, line_size_log2: int, size_log2: int):
    if desc["type"] == "set_assoc":
        return SetAssocMem(desc, line_size_log2, size_log2)

    if desc["type"] == "direct_map":
        return DirectMapMem(line_size_log2, size_log2)

    # TODO(sflur): report error
    assert False


BYTE_UNITS = {"b": 0, "kb": 10, "mb": 20, "gb": 30, "tb": 40}


def parse_bytes_to_log2(x: Union[int, str]) -> int:
    u = 0
    if isinstance(x, str):
        m = re.match(r"^(\d+)\s*(.*)", x)
        assert m
        x = int(m.group(1))
        if m.group(2):
            u = BYTE_UNITS[m.group(2).lower()]

    assert x > 0
    return int(math.log2(x)) + u


class CacheFront:
    """Cache that supports load/store."""

    def __init__(self, desc, parent) -> None:
        self.parent = parent

        # TODO(sflur): report an error if not divisible by 8?
        line_size_log2 = int(
            math.log2(desc["line_size"] // 8))
        size_log2 = parse_bytes_to_log2(desc["size"])
        self.mem = load_mem(desc["placement"], line_size_log2, size_log2)

        self.write_policy = desc["write_policy"]
        self.latencies = desc["latencies"]

        self.front_reqs = collections.deque()
        self.front_replys = collections.defaultdict(collections.deque)
        self.state = None

    def issue_load(self, uid, addr) -> None:
        self.front_reqs.append(("read", uid, addr))

    def issue_store(self, uid, addr) -> None:
        self.front_reqs.append(("write", uid, addr))

    def take_load_replys(self, uid) -> Sequence[int]:
        res = collections.deque()
        replys = self.front_replys[uid]
        for _ in range(len(replys)):
            if replys[0][0] == "read":
                res.append(replys.popleft()[2])
            else:
                replys.rotate()

        if not replys:
            del self.front_replys[uid]

        return res

    def take_store_replys(self, uid) -> Sequence[int]:
        res = collections.deque()
        replys = self.front_replys[uid]
        for _ in range(len(replys)):
            if replys[0][0] == "write":
                res.append(replys.popleft()[2])
            else:
                replys.rotate()

        if not replys:
            del self.front_replys[uid]

        return res

    def tick(self) -> None:
        if self.state:
            if self.state[0] == "stall":
                _, delay, res = self.state
                if delay > 0:
                    self.state = ("stall", delay - 1, res)
                else:
                    self.front_replys[res[1]].append(res)
                    self.state = None

            elif self.state[0] == "miss":
                _, req = self.state
                cmd, _, addr = req
                write_back_addr = self.mem.evict_for(addr)
                if write_back_addr is not None:
                    self.parent.front_reqs.append(
                        ("write", self, write_back_addr))
                self.parent.front_reqs.append((f"fetch_{cmd}", self, addr))
                self.state = ("stall-parent", req)

            elif self.state[0] == "write-through":
                _, req = self.state
                _, _, addr = req
                self.parent.front_reqs.append(("write", self, addr))
                self.state = ("stall-parent", req)

    def tock(self) -> None:
        if not self.state and self.front_reqs:
            req = self.front_reqs.popleft()
            if req[0] in ["read", "write"]:
                cmd, _, addr = req
                if self.mem.try_access(
                        addr, cmd == "write" and
                        self.write_policy == "write_back"):
                    if cmd == "write" and self.write_policy == "write_through":
                        self.state = ("write-through", req)
                    else:
                        self.state = ("stall", self.latencies[cmd] - 1, req)
                else:
                    self.state = ("miss", req)
            else:
                assert False

        if (self.state and self.state[0] == "stall-parent" and
                self.parent.front_replys[self]):
            reply = self.parent.front_replys[self].popleft()
            if reply[0] == "write":
                _, req = self.state
                if self.write_policy == "write_through":
                    self.state = ("stall", self.latencies[req[0]] - 1, req)
                # else: it's a write-back, we still need to wait for the fetch,
                # hence `self.state` stays the same.
            elif reply[0] in ["fetch_read", "fetch_write"]:
                _, req = self.state
                cmd, _, addr = req
                self.mem.insert(
                    addr, cmd == "write" and self.write_policy == "write_back")

                if cmd == "write" and self.write_policy == "write_through":
                    self.state = ("write-through", req)
                else:
                    self.state = ("stall", self.latencies[cmd] - 1, req)
            else:
                assert False


class Cache:
    """Cache that is part of a hierarchy (not front)."""

    def __init__(self, desc, parent) -> None:
        self.parent = parent
        self.children = []

        # TODO(sflur): report an error if not divisible by 8?
        line_size_log2 = int(math.log2(desc["line_size"] // 8))
        size_log2 = parse_bytes_to_log2(desc["size"])
        self.mem = load_mem(desc["placement"], line_size_log2, size_log2)

        self.inclusion = desc["inclusion"]
        self.write_policy = desc["write_policy"]
        self.latencies = desc["latencies"]

        self.front_reqs = collections.deque()
        self.front_replys = collections.defaultdict(collections.deque)
        self.state = None

    def tick(self) -> None:
        if self.state:
            if self.state[0] == "stall":
                _, delay, res = self.state
                if delay > 0:
                    self.state = ("stall", delay - 1, res)
                else:
                    self.front_replys[res[1]].append(res)
                    self.state = None

            elif self.state[0] == "miss":
                _, req = self.state
                if req[0] in ["fetch_read", "fetch_write"]:
                    cmd, _, addr = req
                    if self.inclusion == "inclusive":
                        write_back_addr = self.mem.evict_for(addr)
                        if write_back_addr is not None:
                            self.parent.front_reqs.append(
                                ("write", self, write_back_addr))
                    self.parent.front_reqs.append((cmd, self, addr))
                    self.state = ("stall-parent", req)

                elif req[0] == "write":
                    _, _, addr = req
                    write_back_addr = self.mem.evict_for(addr)
                    if write_back_addr is not None:
                        self.parent.front_reqs.append(
                            ("write", self, write_back_addr))
                    self.parent.front_reqs.append(("fetch_write", self, addr))
                    self.state = ("stall-parent", req)

            elif self.state[0] == "write-through":
                _, req = self.state
                _, _, addr = req
                self.parent.front_reqs.append(("write", self, addr))
                self.state = ("stall-parent", req)

    def tock(self) -> None:
        if not self.state and self.front_reqs:
            req = self.front_reqs.popleft()
            if req[0] in ["fetch_read", "fetch_write"]:
                cmd, _, addr = req
                if self.mem.try_access(addr, False):
                    if self.inclusion == "exclusive":
                        _dirty = self.mem.take(addr)
                    # TODO(sflur): pass the dirty bit
                    self.state = ("stall", self.latencies[cmd] - 1, req)
                else:
                    self.state = ("miss", req)

            elif req[0] == "write":
                cmd, _, addr = req
                if self.mem.try_access(addr, self.write_policy == "write_back"):
                    if self.write_policy == "write_back":
                        self.state = ("stall", self.latencies[cmd] - 1, req)
                    if self.write_policy == "write_through":
                        self.state = ("write-through", req)
                else:
                    self.state = ("miss", req)

            else:
                assert False

        if (self.state and self.state[0] == "stall-parent" and
                self.parent.front_replys[self]):
            reply = self.parent.front_replys[self].popleft()
            if reply[0] == "write":
                _, req = self.state
                if self.write_policy == "write_through":
                    self.state = ("stall", self.latencies[req[0]] - 1, req)
                # else: it's a write-back, we still need to wait for the fetch,
                # hence `self.state` stays the same.
            elif reply[0] in ["fetch_read", "fetch_write"]:
                _, req = self.state
                cmd, _, addr = req
                self.mem.insert(
                    addr, cmd == "write" and self.write_policy == "write_back")

                if cmd == "write" and self.write_policy == "write_through":
                    self.state = ("write-through", req)
                else:
                    self.state = ("stall", self.latencies[cmd] - 1, req)
            else:
                assert False


class MainMemory:
    """Main memory."""

    def __init__(self, desc) -> None:
        self.children = []

        self.latencies = desc["latencies"]

        self.front_reqs = collections.deque()
        self.front_replys = collections.defaultdict(collections.deque)
        self.state = None

    def issue_load(self, uid, addr) -> None:
        self.front_reqs.append(("read", uid, addr))

    def issue_store(self, uid, addr) -> None:
        self.front_reqs.append(("write", uid, addr))

    def take_load_replys(self, uid) -> Sequence[int]:
        res = collections.deque()
        replys = self.front_replys[uid]
        for _ in range(len(replys)):
            if replys[0][0] == "read":
                res.append(replys.popleft()[2])
            else:
                replys.rotate()

        if not replys:
            del self.front_replys[uid]

        return res

    def take_store_replys(self, uid) -> Sequence[int]:
        res = collections.deque()
        replys = self.front_replys[uid]
        for _ in range(len(replys)):
            if replys[0][0] == "write":
                res.append(replys.popleft()[2])
            else:
                replys.rotate()

        if not replys:
            del self.front_replys[uid]

        return res

    def tick(self) -> None:
        if self.state:
            if self.state[0] == "stall":
                _, delay, res = self.state
                if delay > 0:
                    self.state = ("stall", delay - 1, res)
                else:
                    self.front_replys[res[1]].append(res)
                    self.state = None

    def tock(self) -> None:
        if self.state is None and self.front_reqs:
            req = self.front_reqs.popleft()
            if req[0] in ["read", "write", "fetch_read", "fetch_write"]:
                self.state = ("stall", self.latencies[req[0]] - 1, req)
            else:
                assert False


class MemorySystem(interfaces.Module):
    """Memory system model."""

    def __init__(self, desc) -> None:
        super().__init__("MS")

        self.elements = {"main": MainMemory(desc)}

        if "levels" in desc:
            for uid, d in desc["levels"].items():
                self.load_element(uid, d, self.elements["main"])

    def load_element(self, uid, desc, parent) -> None:
        front = "levels" not in desc
        if desc["type"] == "unified":
            e = CacheFront(desc, parent) if front else Cache(desc, parent)
        elif desc["type"] == "dcache":
            e = CacheFront(desc, parent) if front else Cache(desc, parent)
        elif desc["type"] == "icache":
            e = CacheFront(desc, parent) if front else Cache(desc, parent)
        else:
            self.logger.error("unknown cache type: %s", desc['type'])
            sys.exit(1)

        parent.children.append(e)

        self.elements[uid] = e

        if "levels" in desc:
            for u, d in desc["levels"].items():
                self.load_element(u, d, e)

    # Implements interfaces.Module
    # pylint: disable-next=useless-parent-delegation
    def reset(self, cntr: Counter) -> None:
        super().reset(cntr)
        # TODO(sflur): implement proper reset

    # Implements interfaces.Module
    def tick(self, cntr: Counter) -> None:
        super().tick(cntr)

        for e in self.elements.values():
            e.tick()

    # Implements interfaces.Module
    def tock(self, cntr: Counter) -> None:
        super().tock(cntr)

        for e in self.elements.values():
            e.tock()

    # Implements interfaces.Module
    def pending(self) -> int:
        # TODO(sflur): maybe return the number of outstanding accesses?
        return 0

    # Implements interfaces.Module
    def print_state_detailed(self, file) -> None:
        # TODO(sflur): what would be useful to print here?
        pass

    # Implements interfaces.Module
    def get_state_three_valued_header(self) -> Sequence[str]:
        return []

    # Implements interfaces.Module
    def get_state_three_valued(self, vals: Sequence[str]) -> Sequence[str]:
        # TODO(sflur): what would be useful to print here?
        return []
