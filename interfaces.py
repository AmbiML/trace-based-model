import abc
import enum
import collections
import logging
from typing import Generic, Iterable, Optional, Sequence, TypeVar

from counter import Counter
from instruction import Instruction
import tbm_options

class CyclePhase(enum.Enum):
    TICK = enum.auto()
    TOCK = enum.auto()


# Declare type variable
T = TypeVar('T')


class ConsumableQueue(Generic[T], Iterable[T]):
    """A queue that can be consumed by another (not the owner) unit.

    The Iterable iterates over all the visible objects in the queue, oldest to
    newest.
    """

    @property
    @abc.abstractmethod
    def size(self) -> Optional[int]:
        """Total size of the queue."""

    @abc.abstractmethod
    def __len__(self) -> int:
        """Number of elements in the queue."""

    @abc.abstractmethod
    def full(self) -> bool:
        """Check if the queue is full."""

    @abc.abstractmethod
    def dequeue(self) -> Optional[T]:
        """Remove the oldest element in the queue and return it."""

    @abc.abstractmethod
    def peek(self) -> Optional[T]:
        """Return the oldest element in the queue."""


class Module(abc.ABC):
    def __init__(self, name: str):
        self._name = name
        self._cycle = None
        self._phase = None
        self.logger = logging.getLogger(name)

    @property
    def name(self) -> str:
        return self._name

    @property
    def cycle(self) -> int:
        assert self._cycle is not None
        return self._cycle

    @property
    def phase(self) -> CyclePhase:
        assert self._phase is not None
        return self._phase

    def log(self, message: str) -> None:
        if self._cycle is None:
            self.logger.info("[%s:init] %s", self.name, message)
        elif tbm_options.args.print_from_cycle <= self.cycle:
            assert self.phase is not None
            self.logger.info("[%s:%d:%s] %s",
                             self.name, self.cycle, self.phase.name, message)

    @abc.abstractmethod
    def reset(self, cntr: Counter) -> None:
        # TODO(sflur): implement proper reset for all the subclasses. For now
        # I'm just using this to init the cntr.
        self._cycle = None
        self._phase = None

    @abc.abstractmethod
    def tick(self, cntr: Counter) -> None:
        assert self._cycle is None or self.cycle + 1 == cntr.cycles
        assert self._phase is None or self.phase == CyclePhase.TOCK
        self._cycle = cntr.cycles
        self._phase = CyclePhase.TICK

    @abc.abstractmethod
    def tock(self, cntr: Counter) -> None:
        assert self.phase == CyclePhase.TICK
        self._phase = CyclePhase.TOCK

    @abc.abstractmethod
    def pending(self) -> int:
        """Number of pending instructions."""

    @abc.abstractmethod
    def print_state_detailed(self, file) -> None:
        pass

    @abc.abstractmethod
    def get_state_three_valued_header(self) -> Sequence[str]:
        pass

    @abc.abstractmethod
    def get_state_three_valued(self, vals: Sequence[str]) -> Sequence[str]:
        pass

class FetchUnit(Module):
    @property
    @abc.abstractmethod
    def queue(self) -> ConsumableQueue[Instruction]:
        pass

    @abc.abstractmethod
    def eof(self) -> bool:
        pass

    @abc.abstractmethod
    def branch_resolved(self) -> None:
        pass


class SchedUnit(Module):
    @property
    @abc.abstractmethod
    def queues(self) -> Iterable[ConsumableQueue[Instruction]]:
        pass

    @abc.abstractmethod
    def branch_resolved(self) -> None:
        pass

class ExecUnit(Module):
    @abc.abstractmethod
    def get_issue_queue_id(self, instr: Instruction) -> str:
        pass


class ExecPipeline(Module):
    def __init__(self, name: str, kind: str, issue_queue_id: str,
                 depth: int) -> None:
        super().__init__(name)

        self._kind = kind
        self._issue_queue_id = issue_queue_id
        self._depth = depth

        # Instructions that were retired in the current cycle.
        self._retired_instrs = collections.deque()

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def issue_queue_id(self) -> str:
        return self._issue_queue_id

    @property
    def depth(self) -> int:
        """Pipeline depth (excluding other parts of the unit)."""
        return self._depth

    @property
    def retired_instrs(self) -> Sequence[Instruction]:
        """Instructions that retired in the current phase."""
        return self._retired_instrs

    @abc.abstractmethod
    def try_dispatch(self, instr: Instruction, cntr: Counter) -> bool:
        pass


class Scoreboard(Module):
    def insert_accesses(self, instr: Instruction, *,
                        # keyword-only args:
                        reg_reads: Sequence[str],
                        reg_writes: Sequence[str]) -> None:
        """Record the reg accesses instr intends to execute."""

    def can_read(self, instr: Instruction, regs: Sequence[str]) -> bool:
        """True iff instr can execute the reg reads in the next cycle.

        regs must be a subset of reg_reads of a previously call to
        insert_accesses.
        """

    def read(self, instr: Instruction, regs: Sequence[str]) -> None:
        """Record that instr is executing the reg reads in the next cycle."""

    def can_write(self, instr: Instruction, regs: Sequence[str]) -> bool:
        """True iff instr can execute the reg writes in the next cycle.

        regs must be a subset of reg_writes of a previously call to
        insert_accesses.
        """

    def buff_write(self, instr: Instruction, regs: Sequence[str]) -> None:
        """Record that the reg writes become avilable in the writeback queue in
        the next cycle.
        """

    def write(self, instr: Instruction, regs: Sequence[str]) -> None:
        """Record that the writes become avilable in the reg-file in the
        next cycle.
        """

    def can_issue(self, instr: Instruction) -> bool:
        """True iff instr can be issued in the next cycle.

        This is to prevent deadlocks due to dependency cycles. Note that two
        instructions that are issued to the same staged pipeline have an
        additional order (on top of the rw/ww/wr-dependencies), enforced by the
        pipeline.
        """

    def issue(self, instr: Instruction) -> None:
        """Record that instr is issued in the next cycle."""
