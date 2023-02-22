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

"""ScalarPipe module."""

import collections
from typing import Any, Dict, Sequence, Union

from buffered_queue import BufferedQueue
import counter
from counter import Counter
from instruction import Instruction
import interfaces
import scoreboard


class ScalarPipe(interfaces.ExecPipeline):
    def __init__(self, name:str, kind: str, desc: Dict[str, Any], mem_sys,
                 rf_scoreboards: Dict[str, Union[scoreboard.Preemptive,
                                                 scoreboard.VecPreemptive]]
                 ) -> None:
        super().__init__(name, kind, desc["issue_queue"], desc["depth"])

        # Execution Issue Queues
        self._eiq = BufferedQueue(desc.get("eiq_size"))
        self._can_skip_eiq = desc["can_skip_eiq"]

        # The pipeline
        self._pipelined = desc["pipelined"]
        self._stage = collections.deque([None] * self.depth)

        # The writeback buffer
        self._writebackq = BufferedQueue(desc.get("writeback_buff_size"))

        # Interface to memory
        self._mem = (mem_sys.elements[desc["memory_interface"]]
                     if "memory_interface" in desc else None)

        self._load_stage = desc.get("load_stage")
        self._fixed_load_latency = desc.get("fixed_load_latency")
        self._stalling_loads = {}

        self._store_stage = desc.get("store_stage")
        self._fixed_store_latency = desc.get("fixed_store_latency")
        self._stalling_stores = {}

        self._rf_scoreboards = rf_scoreboards

    def reg_read_stall(self, instr: Instruction) -> bool:
        return any(not self._rf_scoreboards[rf].can_read(instr, regs)
                   for rf, regs in instr.inputs_by_type().items())

    def reg_write_stall(self, instr: Instruction) -> bool:
        return any(not self._rf_scoreboards[rf].can_write(instr, regs)
                   for rf, regs in instr.outputs_by_type().items())

    def sb_reg_read(self, instr: Instruction) -> None:
        for rf, regs in instr.inputs_by_type().items():
            self._rf_scoreboards[rf].read(instr, regs)

    def sb_buff_reg_write(self, instr: Instruction) -> None:
        for rf, regs in instr.outputs_by_type().items():
            self._rf_scoreboards[rf].buff_write(instr, regs)

    def sb_reg_write(self, instr: Instruction) -> None:
        for rf, regs in instr.outputs_by_type().items():
            self._rf_scoreboards[rf].write(instr, regs)

    def do_reg_writeback(self) -> None:
        if self._writebackq:
            if not self.reg_write_stall(self._writebackq[0]):
                instr = self._writebackq.popleft()
                self.sb_reg_write(instr)
                self.retired_instrs.append(instr)

    def stall(self, cntr: Counter) -> bool:
        # Check if last stage needs to do reg writes, and the writeback buffer
        # is full.
        if (self._stage[-1] and self._stage[-1].outputs_by_type() and
            self._writebackq.is_buffer_full()):
            return True

        # Check if memory accesses are waiting for reply.
        if (any(self._stalling_loads.values()) or
            any(self._stalling_stores.values())):
            cntr.scalar_load_store_stall += 1
            return True

        return False

    def do_load(self) -> None:
        if self._load_stage is None:
            return

        if self._stage[self._load_stage]:
            inst = self._stage[self._load_stage]
            # TODO(sflur): handle multiple loads?
            assert len(inst.loads) <= 1
            for load in inst.loads:
                if (inst, load) not in self._stalling_loads:
                    self._mem.issue_load(inst, load)
                    self._stalling_loads[(inst, load)] = None

        if self._stage[self._load_stage + self._fixed_load_latency]:
            inst = self._stage[self._load_stage + self._fixed_load_latency]
            for load in inst.loads:
                if self._stalling_loads[(inst, load)] is None:
                    self._stalling_loads[(inst, load)] = True
            for load in self._mem.take_load_replys(inst):
                self._stalling_loads[(inst, load)] = False

    def do_store(self) -> None:
        if self._store_stage is None:
            return

        if self._stage[self._store_stage]:
            inst = self._stage[self._store_stage]
            # TODO(sflur): handle multiple stores?
            assert len(inst.stores) <= 1
            for store in inst.stores:
                if (inst, store) not in self._stalling_stores:
                    self._mem.issue_store(inst, store)
                    self._stalling_stores[(inst, store)] = None

        if self._stage[self._store_stage + self._fixed_store_latency]:
            inst = self._stage[self._store_stage + self._fixed_store_latency]
            for store in inst.stores:
                if self._stalling_stores[(inst, store)] is None:
                    self._stalling_stores[(inst, store)] = True
            for store in self._mem.take_store_replys(inst):
                self._stalling_stores[(inst, store)] = False

    # Implements interfaces.ExecPipeline
    def reset(self, cntr: Counter) -> None:
        super().reset(cntr)
        # TODO(sflur): implement proper reset
        cntr.utilizations[f"{self.name}.eiq"] = counter.Utilization(
            self._eiq.size)
        cntr.utilizations[f"{self.name}.pipe"] = counter.Utilization(
            len(self._stage))
        cntr.utilizations[f"{self.name}.wbq"] = counter.Utilization(
            self._writebackq.size)

    # Implements interfaces.ExecPipeline
    def tick(self, cntr: Counter) -> None:
        """Move instructions from EIQ to pipeline, to WBQ, to RF.

        Instructions move in lockstep when possible. To achieve lockstep we
        process the elements counter to instruction flow direction.
        """
        super().tick(cntr)

        self.retired_instrs.clear()

        self.do_reg_writeback()

        if not self.stall(cntr):
            # Cleanup self._stalling_loads
            if (self._load_stage is not None and
                    self._stage[self._load_stage + self._fixed_load_latency]):
                inst = self._stage[self._load_stage + self._fixed_load_latency]
                for load in inst.loads:
                    # The assertion holds because self.stall() above is True.
                    assert not self._stalling_loads.get((inst, load), False)
                    del self._stalling_loads[(inst, load)]

            # Cleanup self._stalling_stores
            if (self._store_stage is not None and
                    self._stage[self._store_stage + self._fixed_store_latency]):
                inst = self._stage[self._store_stage +
                                   self._fixed_store_latency]
                for store in inst.stores:
                    # The assertion holds because self.stall() above is True.
                    del self._stalling_stores[(inst, store)]

            # Shift stages
            instr = self._stage.pop()
            if instr:
                if instr.outputs_by_type().items():
                    self._writebackq.buffer(instr)
                    cntr.utilizations[f"{self.name}.wbq"].count += 1
                    self.sb_buff_reg_write(instr)
                else:
                    self.retired_instrs.append(instr)
            self._stage.appendleft(None)

        self.do_load()
        self.do_store()

        # Try to issue instructions from eiq to pipeline, until one succeeds.
        if self.is_ready():
            for _ in range(len(self._eiq)):
                instr = self._eiq.popleft()
                if self.try_issue(instr, cntr):
                    break

                self._eiq.append(instr)

    # Implements interfaces.ExecPipeline
    def tock(self, cntr: Counter) -> None:
        super().tock(cntr)

        self.retired_instrs.clear()

        cntr.utilizations[f"{self.name}.pipe"].occupied += len(
            list(1 for i in self._stage if i))

        self._eiq.flush()
        cntr.utilizations[f"{self.name}.eiq"].occupied += len(self._eiq)

        self._writebackq.flush()
        cntr.utilizations[f"{self.name}.wbq"].occupied += len(self._writebackq)

    # Implements interfaces.ExecPipeline
    def pending(self) -> int:
        eiq_count = len(list(self._eiq.chain()))
        pipe_count = len(list(1 for i in self._stage if i))
        wbq_count = len(list(self._writebackq.chain()))
        return eiq_count + pipe_count + wbq_count

    # Implements interfaces.ExecPipeline
    def try_dispatch(self, instr: Instruction, cntr: Counter) -> bool:
        if self._eiq.is_buffer_full():
            return False

        inputs = instr.inputs_by_type()
        outputs = instr.outputs_by_type()

        for rf in inputs.keys() | outputs.keys():
            reads = inputs.get(rf, [])
            writes = outputs.get(rf, [])
            self._rf_scoreboards[rf].insert_accesses(instr, reg_reads=reads,
                                                     reg_writes=writes)

        if not (self._can_skip_eiq and self.is_ready() and
                self.try_issue(instr, cntr)):
            self._eiq.buffer(instr)
            cntr.utilizations[f"{self.name}.eiq"].count += 1

        if instr.loads or instr.stores:
            cntr.scalar_load_store += 1

        return True

    def is_ready(self) -> bool:
        """Check if the pipe is ready to accept a new instruction."""
        if self._pipelined:
            return self._stage[0] is None

        return all(s is None for s in self._stage)

    def try_issue(self, instr: Instruction, cntr: Counter) -> bool:
        """Issue an instruction."""

        if not all(sb.can_issue(instr) for sb in self._rf_scoreboards.values()):
            return False

        if self.reg_read_stall(instr):
            return False

        assert self._stage[0] is None
        self._stage[0] = instr
        cntr.utilizations[f"{self.name}.pipe"].count += 1

        for sb in self._rf_scoreboards.values():
            sb.issue(instr)

        self.sb_reg_read(instr)

        return True

    # Implements interfaces.ExecPipeline
    def print_state_detailed(self, file) -> None:
        eiq_str = ", ".join(str(i) for i in reversed(list(self._eiq.chain())))
        stages = ", ".join(str(i) if i else "-" for i in self._stage)
        wbq_str = ", ".join(
            str(i) for i in reversed(list(self._writebackq.chain())))

        pipe_str = (f"{eiq_str if eiq_str else '-'}"
                    f" > {stages}"
                    f" > {wbq_str if wbq_str else '-'}")

        print(f"[{self.name}] {pipe_str}", file=file)

    # Implements interfaces.ExecPipeline
    def get_state_three_valued_header(self) -> Sequence[str]:
        return ["eiq", self.kind, "wbq"]

    # Implements interfaces.ExecPipeline
    def get_state_three_valued(self, vals: Sequence[str]) -> Sequence[str]:
        if all(self._stage):
            # Full
            pipe_str = vals[2]
        elif any(self._stage):
            # Partial
            pipe_str = vals[1]
        else:
            # Empty
            pipe_str = vals[0]

        return [self._eiq.pp_three_valued(vals),
                pipe_str,
                self._writebackq.pp_three_valued(vals)]
