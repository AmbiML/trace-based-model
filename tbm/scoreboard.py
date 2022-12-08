"""scoreboard module."""

import collections
import sys
from typing import Any, Dict, Sequence, Tuple

from counter import Counter
from instruction import Instruction
import interfaces


class Preemptive(interfaces.Scoreboard):
    """Scoreboard that stalls functional units."""

    def __init__(self, uid: str, desc: Dict[str, Any]) -> None:
        super().__init__(uid)

        # `None` means unrestricted
        self.read_ports = desc.get("read_ports")
        self.dedicated_read_ports = set(desc.get("dedicated_read_ports", []))

        # `None` means unrestricted
        self.write_ports = desc.get("write_ports")
        self.dedicated_write_ports = set(desc.get("dedicated_write_ports", []))

        # `self.rw_deps[instr][reg]` is the instruction from which `instr`
        # reads `reg`'s value, if that instruction is still in-flight, and
        # `None` otherwise.
        self.rw_deps = {}

        # `self.ww_deps[instr][reg]` is the instruction that writes to `reg`
        # just before `instr` writes to it.
        self.ww_deps = {}

        # `self.wr_deps[instr][reg]` is the set of instructions that must do
        # their reads from `reg` before `instr` does its write to `reg`.
        self.wr_deps = collections.defaultdict(dict)

        # `self.writes[reg]` is the last instruction, so far, that intends to
        # write to `reg`, if that instruction is in-flight, and `None`
        # otherwise.
        self.writes = collections.defaultdict(lambda: None)

        # `self.reads[reg]` is the set of instructions that follow
        # `self.writes[reg]`, and read from `reg`.
        self.reads = collections.defaultdict(set)

        # The set of instructions that have been issued to a functional unit.
        # This is a coarse way of preventing deadlocks.
        self.issued = set()

        # `self.write_buff[instr]` is the set of registers for which `instr`
        # has already computed a write value that can be used in a bypass.
        self.write_buff = collections.defaultdict(set)

        # The number of register reads/writes done so far in the current tick.
        self.used_read_ports = 0
        self.used_write_ports = 0

    def dump(self, file=sys.stdout) -> None:
        print(f"-- Scoreboard {self.name}: --", file=file)

        print(f"read ports: {self.read_ports}", file=file)
        print(f"dedicated read ports: {self.dedicated_read_ports}", file=file)
        print(f"write ports: {self.write_ports}", file=file)
        print(f"dedicated write ports: {self.dedicated_write_ports}", file=file)

        print(f"issued instructions: {', '.join(str(i) for i in self.issued)}",
              file=file)

        def pp_instr(i) -> str:
            if i is None:
                return "None"

            if isinstance(i, Instruction):
                return str(i)

            if isinstance(i, Tuple):
                return f"{pp_instr(i[0])} ({i[1]})"

            return "???"

        for i, deps in self.rw_deps.items():
            print(f"rw {pp_instr(i)}: " +
                  ", ".join(f"({r}: {pp_instr(d)})" for r, d in deps.items()),
                  file=file)

        for i, deps in self.ww_deps.items():
            print(f"ww {pp_instr(i)}: " +
                  ", ".join(f"({r}: {pp_instr(d)})" for r, d in deps.items()),
                  file=file)

        for i, deps in self.wr_deps.items():
            print(f"wr {pp_instr(i)}: " +
                  ", ".join(f"({r}: " + "; ".join(pp_instr(d)
                                                  for d in ds) + ")"
                            for r, ds in deps.items()),
                  file=file)

    # Implements interfaces.Scoreboard
    def insert_accesses(self, instr: Instruction, *,
                        # keyword-only args:
                        reg_reads: Sequence[str],
                        reg_writes: Sequence[str]) -> None:
        for reg in reg_reads:
            # We assume instructions never read their own writes
            assert self.writes[reg] != instr

            self.rw_deps.setdefault(
                instr, {}
            )[reg] = self.writes[reg]
            self.reads[reg].add(instr)

        for reg in reg_writes:
            # Can instructions write twice to the same reg?
            assert self.writes[reg] != instr

            self.ww_deps.setdefault(
                instr, {}
            )[reg] = self.writes[reg]
            self.wr_deps.setdefault(instr, {}).setdefault(
                reg, set()).update(self.reads[reg] - {instr})

            self.writes[reg] = instr
            self.reads[reg].clear()

    def read_port_regs(self, instr, regs):
        """Return the regs that need to use a non-dedicated read port."""
        return [
            r for r in regs if
            (r not in self.dedicated_read_ports
             # rw_deps which are not None will be read from
             # the write-buffer.
             # TODO(sflur): what are the restrictions on the
             # write-buffer?
             and self.rw_deps[instr][r] is None)
        ]

    def check_read_ports(self, instr, regs) -> bool:
        if self.read_ports is None:
            return True

        return (self.used_read_ports + len(self.read_port_regs(instr, regs)) <=
                self.read_ports)

    # Implements interfaces.Scoreboard
    def can_read(self, instr, regs) -> bool:
        if not self.check_read_ports(instr, regs):
            return False

        for reg in regs:
            dep = self.rw_deps[instr][reg]
            if dep and reg not in self.write_buff[dep]:
                return False
        return True

    def write_port_regs(self, instr, regs):
        """Return the regs that need to use a non-dedicated write port."""
        # `instr` is not used here, but it is used in the Vec case below.
        del instr
        return [r for r in regs if r not in self.dedicated_write_ports]

    def check_write_ports(self, instr, regs) -> bool:
        if self.write_ports is None:
            return True

        return (self.used_write_ports + len(self.write_port_regs(instr, regs))
                <= self.write_ports)

    # Implements interfaces.Scoreboard
    def can_write(self, instr, regs) -> bool:
        if not self.check_write_ports(instr, regs):
            return False

        return not (any(self.ww_deps[instr][reg] for reg in regs) or
                    any(self.wr_deps[instr][reg] for reg in regs))

    def update_used_read_ports(self, instr, regs) -> None:
        self.used_read_ports += len(self.read_port_regs(instr, regs))

    # Implements interfaces.Scoreboard
    def read(self, instr, regs) -> None:
        self.update_used_read_ports(instr, regs)

        for reg in regs:
            # TODO(sflur): In RVV vec reg groups must be a multiple of the
            # LMUL. Hence, `vadd.vv v0, v1, v2` with LMUL=2 is not a valid
            # instruction (it's actually "reserved"), becasue v1 is not
            # multiple of 2. If we want to support architectures where this is
            # allowed, consider a valid instruction like `vadd.vv v0, v1, v2`
            # with LMUL=2, and 2 slices uArch. Slice v2.0 is read twice, first
            # as part of the group starting from v2, and second as part of the
            # group starting from v1. The `del` below will be executed for the
            # first read and then the second read will fail in `can_read` where
            # we assume it's still in the map.
            del self.rw_deps[instr][reg]

            for rdeps in self.wr_deps.values():
                rdeps.get(reg, set()).discard(instr)

            self.reads[reg].discard(instr)

        if not any(self.rw_deps[instr]):
            del self.rw_deps[instr]
            if instr not in self.ww_deps:
                self.issued.remove(instr)

    # Implements interfaces.Scoreboard
    def buff_write(self, instr, regs) -> None:
        self.write_buff[instr].update(regs)

    def update_used_write_ports(self, instr, regs) -> None:
        self.used_write_ports += len(self.write_port_regs(instr, regs))

    # Implements interfaces.Scoreboard
    def write(self, instr, regs) -> None:
        self.update_used_write_ports(instr, regs)

        for reg in regs:
            del self.ww_deps[instr][reg]
            del self.wr_deps[instr][reg]

            for rdeps in self.rw_deps.values():
                if rdeps.get(reg) == instr:
                    rdeps[reg] = None

            for rdeps in self.ww_deps.values():
                if rdeps.get(reg) == instr:
                    rdeps[reg] = None

            if self.writes[reg] == instr:
                self.writes[reg] = None

        if not any(self.ww_deps[instr]):
            del self.ww_deps[instr]
            del self.wr_deps[instr]
            if instr not in self.rw_deps:
                self.issued.remove(instr)

        self.write_buff.pop(instr, None)

    # Implements interfaces.Scoreboard
    def can_issue(self, instr) -> bool:
        if (instr not in self.rw_deps and instr not in self.ww_deps and
                instr not in self.wr_deps):
            return True

        if any(d and d not in self.issued
               for d in self.rw_deps.get(instr, {}).values()):
            return False

        if any(d and d not in self.issued
               for d in self.ww_deps.get(instr, {}).values()):
            return False

        if any(d not in self.issued
               for ds in self.wr_deps.get(instr, {}).values()
               for d in ds):
            return False

        return True

    # Implements interfaces.Scoreboard
    def issue(self, instr) -> None:
        if (instr not in self.rw_deps and instr not in self.ww_deps and
                instr not in self.wr_deps):
            return

        self.issued.add(instr)

    def clear_used_ports(self) -> None:
        self.used_read_ports = 0
        self.used_write_ports = 0

    # Implements interfaces.Scoreboard
    # pylint: disable-next=useless-parent-delegation
    def reset(self, cntr: Counter) -> None:
        super().reset(cntr)

    # Implements interfaces.Scoreboard
    # pylint: disable-next=useless-parent-delegation
    def tick(self, cntr: Counter) -> None:
        super().tick(cntr)

    # Implements interfaces.Scoreboard
    def tock(self, cntr: Counter) -> None:
        super().tock(cntr)
        self.clear_used_ports()

    # Implements interfaces.Scoreboard
    def pending(self) -> int:
        # TODO(sflur): implement?
        assert False

    # Implements interfaces.Scoreboard
    def print_state_detailed(self, file) -> None:
        # TODO(sflur): implement?
        assert False

    # Implements interfaces.Scoreboard
    def get_state_three_valued_header(self) -> Sequence[str]:
        # TODO(sflur): implement?
        assert False

    # Implements interfaces.Scoreboard
    def get_state_three_valued(self, vals: Sequence[str]) -> Sequence[str]:
        # TODO(sflur): implement?
        assert False


class VecPreemptive(Preemptive):
    """Scoreboard for vector registers.

    Each register is sliced to multiple slices.
    """

    def __init__(self, uid: str, desc: Dict[str, Any], slices: int) -> None:
        super().__init__(uid, desc)

        self.slices = slices

        self.used_read_ports = [0] * slices
        self.used_write_ports = [0] * slices

    def read_port_regs(self, instr,
                       regs: Sequence[str]) -> Dict[int, Sequence[str]]:
        """Return the regs that need to use a non-dedicated read port."""

        res = {}

        for rs in regs:
            r, _, s = rs.rpartition(".")
            if (r not in self.dedicated_read_ports
                    # rw_deps which are not None will be read from
                    # the write-buffer.
                    # TODO(sflur): what are the restrictions on the
                    # write-buffer?
                    and self.rw_deps[instr][rs] is None):
                res.setdefault(int(s), collections.deque()).append(r)

        return res

    def check_read_ports(self, instr, regs) -> bool:
        if self.read_ports is None:
            return True

        regs = self.read_port_regs(instr, regs)

        return all(self.used_read_ports[s] + len(rs) <= self.read_ports
                   for s, rs in regs.items())

    def write_port_regs(self, instr: Instruction,
                        regs: Sequence[str]) -> Dict[int, Sequence[str]]:
        """Return the regs that need to use a non-dedicated write port."""

        res = {}

        for rs in regs:
            r, _, s = rs.rpartition(".")
            if r not in self.dedicated_write_ports:
                res.setdefault(int(s), collections.deque()).append(r)

        return res

    def check_write_ports(self, instr, regs) -> bool:
        if self.write_ports is None:
            return True

        regs = self.write_port_regs(instr, regs)

        return all(self.used_write_ports[s] + len(rs) <= self.write_ports
                   for s, rs in regs.items())

    def update_used_read_ports(self, instr, regs) -> None:
        for s, rs in self.read_port_regs(instr, regs).items():
            self.used_read_ports[s] += len(rs)

    def update_used_write_ports(self, instr, regs) -> None:
        for s, rs in self.write_port_regs(instr, regs).items():
            self.used_write_ports[s] += len(rs)

    def clear_used_ports(self) -> None:
        for s in range(self.slices):
            self.used_read_ports[s] = 0
            self.used_write_ports[s] = 0
