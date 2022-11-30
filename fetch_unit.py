"""Fetch Unit module."""

from typing import Any, Dict, Optional, Sequence

from buffered_queue import BufferedQueue
import counter
from counter import Counter
from functional_trace import FunctionalTrace
import interfaces

class NextFetch:
    """Hold the sate of next-addr fetching.

    `addr` is the memory location from which the next batch of instructions
    should be fetched from. This can be None if there are no more instructions
    in the trace, or when the next instruction (after a branch) is not the
    normal +4 bytes successor.
    """

    def __init__(self) -> None:
        self._addr = None
        self._stall = False

    @property
    def addr(self) -> Optional[int]:
        return self._addr

    @addr.setter
    def addr(self, val: int) -> None:
        self._addr = val
        self._stall = False

    @property
    def stall(self) -> bool:
        return self._stall

    @stall.setter
    def stall(self, val: bool) -> None:
        self._addr = None
        self._stall = val


class FetchUnit(interfaces.FetchUnit):
    def __init__(self, config: Dict[str, Any], trace: FunctionalTrace):
        super().__init__("FE")

        self._trace = trace
        self._branch_prediction = config["branch_prediction"]
        self._fetch_rate = config["fetch_rate"]


        ## Current state
        # The queue from which `SchedUnit` reads.
        self._queue = BufferedQueue(config.get("fetch_queue_size"))

        # The next address to fetch a batch from, or indicate a stall (waiting
        # for branch target to be computed).
        self._next_fetch_addr = NextFetch()

        ## Next state
        self._next_fetch_stall = None

    # Implements interfaces.FetchUnit
    @property
    def queue(self) -> interfaces.ConsumableQueue:
        return self._queue

    # Implements interfaces.FetchUnit
    def eof(self) -> bool:
        return self._trace.eof()

    # Implements interfaces.FetchUnit
    def pending(self) -> int:
        return len(self._queue)

    # Implements interfaces.FetchUnit
    def reset(self, cntr: Counter) -> None:
        super().reset(cntr)
        # TODO(sflur): implement proper reset
        cntr.stalls[self.name] = 0
        cntr.utilizations[self.name] = counter.Utilization(self.queue.size)

    # Implements interfaces.FetchUnit
    def tick(self, cntr: Counter) -> None:
        super().tick(cntr)

        if self._trace.eof():
            self.log("can't fetch new instructions:"
                     " no more instructions in trace.")
            return

        if (self._queue.size is not None and
                len(self._queue) + self._fetch_rate >
                self._queue.size):
            self.log("can't fetch new instructions:"
                     " not enough room in the fetch queue.")
            cntr.stalls[self.name] += 1
            return

        # TODO(sflur): make `inst_size` configurable.
        inst_size = 4  # bytes

        if self._next_fetch_addr.addr is not None:
            if self._trace.next_addr() != self._next_fetch_addr.addr:
                if self._branch_prediction == "none":
                    self.log(
                        "generating memory accesses for"
                        f" {self._next_fetch_addr.addr} (but next trace"
                        f" instruction is at {self._trace.next_addr()})")

                    # TODO(sflur): generate memory accesses for the whole batch.

                    self._next_fetch_addr.stall = True
                    return

                assert self._branch_prediction == "perfect", (
                        # pylint: disable-next=consider-using-f-string
                        "Error: Unknown branch prediction option %s" %
                        self._branch_prediction)

        elif self._next_fetch_addr.stall:
            self.log("stalling")
            cntr.stalls[self.name] += 1
            return

        # The first address of the current batch. After a branch this might not
        # be properly aligned. We should still generate memory accesses for the
        # missing lower bytes!
        fetch_addr = self._trace.next_addr()
        # TODO(sflur): generate memory accesses for the whole batch.

        # TODO(sflur): handle compressed instructions, and misaligned
        # instructions?

        # Set the address for the next batch, and force it to be aligned.
        next_addr = fetch_addr + (inst_size * self._fetch_rate)
        next_addr -= next_addr % (inst_size * self._fetch_rate)
        self._next_fetch_addr.addr = next_addr

        # Buffer the current batch of instructions.
        for fetch_addr in range(fetch_addr, next_addr, inst_size):
            if fetch_addr != self._trace.next_addr():
                # This instruction was not executed in the functional trace,
                # hence it's not in the trace. But, a uarch would fetch this
                # instruction from memory, and it would occupy a place in the
                # queue, so we simulate that (with a None).
                self._queue.buffer(None)
                continue

            inst = self._trace.dequeue()
            if inst is None:
                self.log("no more instructions in trace")
                break

            self.log(inst.mnemonic + " from mem/trace")
            self._queue.buffer(inst)

            if (not inst.is_branch and
                    inst.addr + inst_size != self._trace.next_addr()):
                # This could happen when an exception is taken
                # TODO(sflur): what do we need to do to handle an exception?
                self.log("next fetch is an exception handler?")
                self._next_fetch_addr.addr = self._trace.next_addr()

        # We count all the instructions a uarch would actually fetch.
        cntr.utilizations[self.name].count += self._fetch_rate

    # Implements interfaces.FetchUnit
    def tock(self, cntr: Counter) -> None:
        super().tock(cntr)

        self._queue.flush()

        if self._next_fetch_stall is not None:
            self._next_fetch_addr.stall = self._next_fetch_stall
            self._next_fetch_stall = None

        cntr.utilizations[self.name].occupied += len(self._queue)

    # Implements interfaces.FetchUnit
    def branch_resolved(self) -> None:
        """Inform the FU that branch target is now avilable."""
        self.log("branch resolved")

        assert self._branch_prediction == "none"

        # The branch target might have already been placed in the the fetch
        # queue, so we only clean Nones (fake instructions) from the queue.
        while self._queue.buff and self._queue.buff[0] is None:
            self._queue.buff.popleft()
        while self._queue and self._queue[0] is None:
            self._queue.popleft()

        if self.phase == interfaces.CyclePhase.TICK:
            self._next_fetch_stall = False
        else:
            assert self.phase == interfaces.CyclePhase.TOCK
            self._next_fetch_addr.stall = False

    # Implements interfaces.FetchUnit
    def print_state_detailed(self, file) -> None:
        if self._queue:
            queue_str = ", ".join(str(i) if i else "X"
                                  # pylint: disable-next=bad-reversed-sequence
                                  for i in reversed(self._queue))
        else:
            queue_str = "-"
        print(f"[{self.name}] {queue_str}", file=file)

    # Implements interfaces.FetchUnit
    def get_state_three_valued_header(self) -> Sequence[str]:
        return [self.name]

    # Implements interfaces.FetchUnit
    def get_state_three_valued(self,vals: Sequence[str]) ->  Sequence[str]:
        return [self._queue.pp_three_valued(vals)]
