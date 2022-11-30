"""VectorPipe module."""

import collections
import math
from typing import Any, Dict, Sequence, Optional, Tuple, Union

from buffered_queue import BufferedQueue
import counter
from counter import Counter
import instruction
from instruction import Instruction
import interfaces
import scoreboard
import utilities


class VectorPipe(interfaces.ExecPipeline):
    """Vector pipe model.

    This pipeline supports flexible chaining and tailgating.

    That is
    - The vector register is split into a number of slices.
    - Once a vector instruction starts producing result slices,
      they are written to the register file in order at one slice per cycle.
    - (See ScoreboardVector for more on issuing vector instructions.)

    Scalar input registers are read at the start of the instruction (first
    cycle of first slice).

    Scalar output register are written to at the end of the instruction (last
    cycle of the last slice).
    """

    def __init__(self, name: str, kind: str, desc: Dict[str, Any], slices: int,
                 mem_sys,
                 rf_scoreboards: Dict[str, Union[scoreboard.Preemptive,
                                                 scoreboard.VecPreemptive]]
                 ) -> None:
        super().__init__(name, kind, desc["issue_queue"], desc["depth"])

        # Execution Issue Queues
        self._eiq = BufferedQueue(desc.get("eiq_size"))
        self._can_skip_eiq = desc["can_skip_eiq"]

        # The pipeline
        self._slices = slices
        self._pipelined = desc["pipelined"]
        self._stage = collections.deque([None] * self.depth)
        self._inflight_instr = None
        self._inflight_next_slice = 0

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

    def eslices(self, instr: Instruction) -> int:
        """The number of slices required to execute `instr`."""
        return math.ceil(instr.max_emul() * self._slices)

    def slice(self, accesses: Sequence[int], index: int,
              eslices: int) -> Tuple[int, int]:
        """The memory access location and size, of a given slice.

        Args:
          accesses: a sequence of memory locations, one for each vector element.
          index: the index of the required slice.
          eslices: the total number of slices `accesses` should be split to.

        # TODO(sflur): return the access size in bytes, instead of `count`.
        Return: (access, count), where access is the first byte of the memory
        location that should be accessed, and count is the number of elements
        that should be accessed.
        """
        alen = len(accesses)
        slen = alen // eslices
        start = index * slen
        slen = min(slen, alen - start)
        return (accesses[start], slen)

    def reg_read_stall(self, instr: Instruction, s: int) -> bool:
        return any(not self._rf_scoreboards[rf].can_read(instr, seq[s])
                   for rf, seq in self.input_seq_by_type(instr).items()
                   if len(seq) > s)

    def reg_write_stall(self, instr: Instruction, s: int) -> bool:
        return any(not self._rf_scoreboards[rf].can_write(instr, seq[s])
                   for rf, seq in self.output_seq_by_type(instr).items()
                   if len(seq) > s)

    def sb_reg_read(self, instr: Instruction, s: int) -> None:
        for rf, seq in self.input_seq_by_type(instr).items():
            if len(seq) > s and seq[s]:
                self._rf_scoreboards[rf].read(instr, seq[s])

    def sb_buff_reg_write(self, instr: Instruction, s: int) -> None:
        for rf, seq in self.output_seq_by_type(instr).items():
            if len(seq) > s:
                self._rf_scoreboards[rf].buff_write(instr, seq[s])

    def sb_reg_write(self, instr: Instruction, s: int) -> None:
        for rf, seq in self.output_seq_by_type(instr).items():
            if len(seq) > s:
                self._rf_scoreboards[rf].write(instr, seq[s])

    def do_reg_writeback(self) -> None:
        if self._writebackq:
            instr, s = self._writebackq[0]
            if not self.reg_write_stall(instr, s):
                self.sb_reg_write(instr, s)
                self._writebackq.popleft()
                if s + 1 == self.eslices(instr):
                    self.retired_instrs.append(instr)

    def stall(self, cntr: Counter) -> bool:
        # Check if last stage needs to do reg writes, and the writeback buffer
        # is full.
        if (self._stage[-1] and any(
                self._stage[-1][1] < len(seq) and seq[self._stage[-1][1]]
                for _, seq in
                  self.output_seq_by_type(self._stage[-1][0]).items()) and
            self._writebackq.is_buffer_full()):
            return True

        # Check if memory accesses are waiting for reply.
        if (any(self._stalling_loads.values()) or
            any(self._stalling_stores.values())):
            cntr.vector_load_store_stall += 1
            return True

        return False

    def do_load(self) -> None:
        if self._load_stage is None:
            return

        if (self._stage[self._load_stage] and
            self._stage[self._load_stage][0].loads):
            instr, s = self._stage[self._load_stage]
            load, _size = self.slice(instr.loads, s, self.eslices(instr))
            if (instr, s, load) not in self._stalling_loads:
                # TODO(sflur): pass size to issue_load
                self._mem.issue_load((instr, s), load)
                self._stalling_loads[(instr, s, load)] = None

        if (self._stage[self._load_stage + self._fixed_load_latency] and
                self._stage[self._load_stage +
                            self._fixed_load_latency][0].loads):
            instr, s = self._stage[self._load_stage + self._fixed_load_latency]
            load, _ = self.slice(instr.loads, s, self.eslices(instr))
            if self._stalling_loads[(instr, s, load)] is None:
                self._stalling_loads[(instr, s, load)] = True
            for load in self._mem.take_load_replys((instr, s)):
                self._stalling_loads[(instr, s, load)] = False

    def do_store(self) -> None:
        if self._store_stage is None:
            return

        if self._stage[self._store_stage] and self._stage[
                self._store_stage][0].stores:
            instr, s = self._stage[self._store_stage]
            store, _size = self.slice(instr.stores, s, self.eslices(instr))
            if (instr, s, store) not in self._stalling_stores:
                # TODO(sflur): pass size to issue_store
                self._mem.issue_store((instr, s), store)
                self._stalling_stores[(instr, s, store)] = None

        if (self._stage[self._store_stage + self._fixed_store_latency] and
                self._stage[self._store_stage +
                           self._fixed_store_latency][0].stores):
            instr, s = self._stage[self._store_stage +
                                   self._fixed_store_latency]
            store, _ = self.slice(instr.stores, s, self.eslices(instr))
            if self._stalling_stores[(instr, s, store)] is None:
                self._stalling_stores[(instr, s, store)] = True
            for store in self._mem.take_store_replys((instr, s)):
                self._stalling_stores[(instr, s, store)] = False

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
        super().tick(cntr)

        self.retired_instrs.clear()

        self.do_reg_writeback()

        if not self.stall(cntr):
            # Cleanup self.stalling_loads
            if (self._load_stage is not None and
                    self._stage[self._load_stage + self._fixed_load_latency] and
                    self._stage[self._load_stage +
                               self._fixed_load_latency][0].loads):
                instr, s = self._stage[self._load_stage +
                                       self._fixed_load_latency]
                load, _ = self.slice(instr.loads, s, self.eslices(instr))
                # The assertion holds because self.stall() above is True.
                assert not self._stalling_loads.get((instr, s, load), False)
                del self._stalling_loads[(instr, s, load)]

            # Cleanup self.stalling_stores
            if (self._store_stage is not None and
                    self._stage[self._store_stage +
                                self._fixed_store_latency] and
                    self._stage[self._store_stage +
                               self._fixed_store_latency][0].stores):
                instr, s = self._stage[self._store_stage +
                                      self._fixed_store_latency]
                store, _ = self.slice(instr.stores, s, self.eslices(instr))
                # The assertion holds because self.stall() above is True.
                assert not self._stalling_stores.get((instr, s, store), False)
                del self._stalling_stores[(instr, s, store)]

            # Shift stages
            st = self._stage.pop()
            if st:
                instr, s = st
                if any(
                        len(seq) > s and seq[s]
                        for _, seq in self.output_seq_by_type(instr).items()):
                    self._writebackq.buffer((instr, s))
                    cntr.utilizations[f"{self.name}.wbq"].count += 1
                    self.sb_buff_reg_write(instr, s)
                elif s + 1 == self.eslices(instr):
                    self.retired_instrs.append(instr)

            # Issue the next slice into the piprline.
            if (self._inflight_instr and not self.reg_read_stall(
                    self._inflight_instr, self._inflight_next_slice)):
                self.sb_reg_read(self._inflight_instr,
                                 self._inflight_next_slice)

                self._stage.appendleft(
                    (self._inflight_instr, self._inflight_next_slice))
                cntr.utilizations[f"{self.name}.pipe"].count += 1
                self._inflight_next_slice += 1
                if self._inflight_next_slice == self.eslices(
                        self._inflight_instr):
                    self._inflight_instr = None
            else:
                self._stage.appendleft(None)

        self.do_load()
        self.do_store()

        # Try to issue each of the instructions in `eiq`.
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

        inputs = self.input_seq_by_type(instr)
        outputs = self.output_seq_by_type(instr)

        for rf in inputs.keys() | outputs.keys():
            reads = utilities.flatten(inputs.get(rf, []))
            writes = utilities.flatten(outputs.get(rf, []))
            self._rf_scoreboards[rf].insert_accesses(instr, reg_reads=reads,
                                                     reg_writes=writes)

        if not (self._can_skip_eiq and self.is_ready() and
                self.try_issue(instr, cntr)):
            self._eiq.buffer(instr)
            cntr.utilizations[f"{self.name}.eiq"].count += 1

        if instr.loads or instr.stores:
            cntr.vector_load_store += 1

        return True

    def is_ready(self) -> bool:
        """Check if the pipe is ready to accept a new instruction."""
        if self._pipelined:
            return self._inflight_instr is None and self._stage[0] is None

        return self._inflight_instr is None and all(
            s is None for s in self._stage)

    def try_issue(self, instr: Instruction, cntr: Counter) -> bool:
        """Issue an instruction."""

        if not all(sb.can_issue(instr) for sb in self._rf_scoreboards.values()):
            return False

        if self.reg_read_stall(instr, 0):
            return False

        assert self._stage[0] is None
        self._stage[0] = (instr, 0)
        cntr.utilizations[f"{self.name}.pipe"].count += 1

        assert self._inflight_instr is None
        if 1 < self.eslices(instr):
            self._inflight_instr = instr
            self._inflight_next_slice = 1

        for sb in self._rf_scoreboards.values():
            sb.issue(instr)

        self.sb_reg_read(instr, 0)

        return True

    def vec_reg_seq(self, reg: str, input_reg: bool, emul: Union[int, float],
                    max_emul: Union[int, float]) -> Sequence[Optional[str]]:
        base = int(reg[1:])
        if emul < 1:
            seq = [f"{reg}.{s}" for s in range(math.ceil(emul * self._slices))]
        else:
            emul = int(emul)
            seq = [
                f"{reg[0]}{base + g}.{s}" for g in range(emul)
                for s in range(self._slices)
            ]

        if (emul == max_emul or (emul < 1 and self._slices < 1 / emul)):
            return seq

        assert int(max_emul / emul) == 2

        # Interleave Nones with `seq`
        if input_reg:
            seq = zip(seq, [None] * len(seq))
        else:
            seq = zip([None] * len(seq), seq)
        return utilities.flatten(seq)

    def input_seq(self, instr: Instruction, reg: str) -> Sequence[str]:
        if instruction.is_vector_register(reg):
            assert instr.lmul is not None

            # TODO(sflur): anymore cases of input widening?
            if ((instr.mnemonic.endswith(".wv") or
                 instr.mnemonic.endswith(".wx") or
                 instr.mnemonic.endswith(".wf") or
                 instr.mnemonic.endswith(".wi")) and instr.operands[1] == reg):
                emul = 2 * instr.lmul
            else:
                emul = instr.lmul

            return self.vec_reg_seq(reg, True, emul, instr.max_emul())

        return [reg]

    def output_seq(self, instr: Instruction, reg: str) -> Sequence[str]:
        if instruction.is_vector_register(reg):
            assert instr.lmul is not None

            # TODO(sflur): anymore cases of output widening?
            if ((instr.mnemonic.startswith("vw") or
                 instr.mnemonic.startswith("vfw")) and
                    instr.operands[0] == reg):
                emul = 2 * instr.lmul
            else:
                emul = instr.lmul

            return self.vec_reg_seq(reg, False, emul, instr.max_emul())

        res = [None] * self.eslices(instr)
        res[-1] = reg
        return res

    def input_seq_by_type(
            self, instr: Instruction) -> Dict[str, Sequence[Sequence[str]]]:
        """Compute a map from register-files to sequences of input register
        sets.

        A register file is mapped to a sequence of sets, where set i is the set
        of registers that will be read from by slice i.
        """
        res = {}

        for ty, regs in instr.inputs_by_type().items():
            seq = [collections.deque() for _ in range(self.eslices(instr))]

            for reg in regs:
                for i, r in enumerate(self.input_seq(instr, reg)):
                    if r:
                        seq[i].append(r)

            res[ty] = seq

        return res

    def output_seq_by_type(
            self, instr: Instruction) -> Dict[str, Sequence[Sequence[str]]]:
        """Compute a map from register-files to sequences of output register
        sets.

        A register file is mapped to a sequence of sets, where set i is the set
        of registers that will be written to by slice i.
        """
        res = {}

        for ty, regs in instr.outputs_by_type().items():
            seq = [collections.deque() for _ in range(self.eslices(instr))]

            for reg in regs:
                for i, r in enumerate(self.output_seq(instr, reg)):
                    if r:
                        seq[i].append(r)

            res[ty] = seq

        return res

    # Implements interfaces.ExecPipeline
    def print_state_detailed(self, file) -> None:
        eiq_str = ", ".join(str(i) for i in reversed(list(self._eiq.chain())))
        stages = ", ".join(f"{i[0]} ({i[1]})" if i else "-"
                           for i in self._stage)
        wbq_str = ", ".join(f"{i[0]} ({i[1]})"
                            for i in reversed(list(self._writebackq.chain())))

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
