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

from typing import Any, Dict, Iterable, Sequence

from buffered_queue import BufferedQueue
import counter
from counter import Counter
from instruction import Instruction
import interfaces


# TODO(b/261690182): rename the SchedUnit
class SchedUnit(interfaces.SchedUnit):
    """Issue unit model."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("SC")

        self._decode_rate = config.get("decode_rate")
        self._branch_prediction = config["branch_prediction"]

        self._fetch_unit = None
        self._exec_unit = None

        ## Current states
        self._queues = {}
        self._branch_stalling = False

        ## Next state
        self._next_branch_stalling = None

    def add_queue(self, uid: str, desc) -> None:
        self._queues[uid] = BufferedQueue(desc.get("size"))

    def connect(self, fetch_unit: interfaces.FetchUnit,
                exec_unit: interfaces.ExecUnit) -> None:
        self._fetch_unit = fetch_unit
        self._exec_unit = exec_unit

    # Implements interfaces.SchedUnit
    @property
    def queues(self) -> Iterable[BufferedQueue[Instruction]]:
        return self._queues.values()

    # Implements interfaces.SchedUnit
    def pending(self) -> int:
        return sum(len(q) for q in self._queues.values())

    # Implements interfaces.SchedUnit
    def reset(self, cntr: Counter) -> None:
        super().reset(cntr)
        # TODO(sflur): implement proper reset
        cntr.stalls[self.name] = 0
        for uid, q in self._queues.items():
            cntr.utilizations[uid] = counter.Utilization(q.size)

    # Implements interfaces.SchedUnit
    def tick(self, cntr: Counter) -> None:
        super().tick(cntr)

        if self._branch_stalling:
            self.log("queuing stalled: unresolved branch")
            return

        for _ in range(self._decode_rate if self._decode_rate
                       else len(self._fetch_unit.queue)):
            if not self._fetch_unit.queue:
                # Fetch queue is empty
                break

            fetched_instr = self._fetch_unit.queue.peek()
            if not fetched_instr:
                # A None instruction in the fetch queue stands for instruction
                # that the functional simulator did not execute (or fetch), so
                # we don't know what instruction that was, or how it behaved.
                # In a real uarch this instruction will take some resources
                # until the uarch figures out it should be evicted.
                # TODO(sflur): count these instructions and apply some
                # proportional penalty to the performance TBM reports?
                self._fetch_unit.queue.dequeue()
                continue

            # Check if we need to flush pending instructions.
            if fetched_instr.is_flush and (self.pending() or
                                           self._exec_unit.pending()):
                # TODO(sflur): Currently flush instructions wait in the fetch
                # queue, is that the right place to wait in?
                cntr.stalls[self.name] += 1
                self.log(f"queueing stalled: flush in effect: {fetched_instr}")
                break

            if fetched_instr.is_nop:
                self.log(f"retired NOP instruction: {fetched_instr}")
                self._fetch_unit.queue.dequeue()
                cntr.retired_instruction_count += 1
                continue

            qid = self._exec_unit.get_issue_queue_id(fetched_instr)

            # Check if the queue is available.
            if self._queues[qid].is_buffer_full():
                cntr.stalls[self.name] += 1
                self.log(f"queueing stalled: '{qid}' is full")
                break

            # TODO(sflur): instead of check_conflicts, we could add the
            # instructions to the scoreboard at this point.
            if not self.check_conflicts(fetched_instr, qid):
                # TODO(sflur): the blocking instruction is still in the fetch
                # queue, maybe move it somewhere else?
                cntr.stalls[self.name] += 1
                self.log("queueing stalled: conflict with queued instruction")
                break

            # It is safe to queue the instruction.
            self._queues[qid].buffer(fetched_instr)
            self._fetch_unit.queue.dequeue()
            cntr.utilizations[qid].count += 1
            self.log(f"instruction '{fetched_instr}' queued")

            if fetched_instr.is_branch:
                cntr.branch_count += 1

                if self._branch_prediction == "none":
                    self._branch_stalling = True
                    break

    # Implements interfaces.SchedUnit
    def tock(self, cntr: Counter) -> None:
        super().tock(cntr)

        for q in self._queues.values():
            q.flush()

        if self._next_branch_stalling is not None:
            self._branch_stalling = self._next_branch_stalling
            self._next_branch_stalling = None

        for uid, q in self._queues.items():
            cntr.utilizations[uid].occupied += len(q)

    def check_conflicts(self, new_instr: Instruction, qid: str) -> bool:
        """Check if `instr` conflicts with other instructions.

        Check whether it is safe to reorder `instr` wrt the instructions
        already in other queues.
        There is no need to check conflicts with instructions that are already
        in execution pipes, as that is handled by the scoreboard.

        Args:
          new_instr: fetched instructions.
          qid: the dispatch queue the instruction will be placed in.
        Returns:
          True if there are no conflicts, False otherwise.
        """

        for name, q in self._queues.items():
            if name == qid:
                # skip the queue new_instr is going to, as it's an in-order
                # queue.
                continue

            for instr in q.chain():
                if new_instr.conflicts_with(instr):
                    return False

        return True

    # Implements interfaces.SchedUnit
    def branch_resolved(self) -> None:
        if self.phase == interfaces.CyclePhase.TICK:
            self._next_branch_stalling = False
        else:
            assert self.phase == interfaces.CyclePhase.TOCK
            self._branch_stalling = False

    # Implements interfaces.SchedUnit
    def print_state_detailed(self, file) -> None:
        for uid, dq in self._queues.items():
            if dq:
                queue_str = ", ".join(str(i) for i in reversed(dq))
            else:
                queue_str = "-"
            print(f"[qu-{uid}] {queue_str}", file=file)


    # Implements interfaces.SchedUnit
    def get_state_three_valued_header(self) -> Sequence[str]:
        return self._queues.keys()

    # Implements interfaces.SchedUnit
    def get_state_three_valued(self,vals: Sequence[str]) ->  Sequence[str]:
        return [q.pp_three_valued(vals) for q in self._queues.values()]
