import collections
import sys
from typing import Any, Dict, Sequence, Union

from counter import Counter
from instruction import Instruction
import interfaces
import scoreboard

class ExecUnit(interfaces.ExecUnit):
    """Execution unit model."""

    def __init__(
        self, config: Dict[str, Any], pipe_map: Dict[str, str],
        rf_scoreboards: Dict[str, Union[scoreboard.Preemptive,
                                        scoreboard.VecPreemptive]]
    ):
        super().__init__("EX")

        self._branch_prediction = config["branch_prediction"]

        self._fetch_unit = None
        self._sched_unit = None

        self._pipe_map = pipe_map

        # State
        self._rf_scoreboards = rf_scoreboards
        self._pipes = {}
        self._retired_instructions = collections.deque()

    def add_pipe(self, kind: str,
                 pipes: Sequence[interfaces.ExecPipeline]) -> None:
        assert pipes
        assert kind not in self._pipes
        self._pipes[kind] = pipes

    def connect(self, fetch_unit: interfaces.FetchUnit,
                sched_unit: interfaces.SchedUnit) -> None:
        self._fetch_unit = fetch_unit
        self._sched_unit = sched_unit

    # Implements interfaces.ExecUnit
    def pending(self) -> int:
        return sum(p.pending() for ps in self._pipes.values() for p in ps)

    # Implements interfaces.ExecUnit
    def get_issue_queue_id(self, instr: Instruction) -> str:
        kind = self.get_functional_unit(instr)
        return self._pipes[kind][0].issue_queue_id

    def get_functional_unit(self, instr: Instruction) -> str:
        """Return the functional unit kind the instruction will execute in."""
        try:
            return self._pipe_map[instr.mnemonic]
        except KeyError:
            self.logger.error("unknown pipe for instruction '%s'",
                              instr.mnemonic)
            sys.exit(1)

    # Implements interfaces.ExecUnit
    def reset(self, cntr: Counter) -> None:
        super().reset(cntr)
        # TODO(sflur): implement proper reset
        for ps in self._pipes.values():
            for p in ps:
                p.reset(cntr)

    # Implements interfaces.ExecUnit
    def tick(self, cntr: Counter) -> None:
        """Move instructions from dispatch queues, in sched_unit, to functional
        units.

        Instructions move in lockstep when possible. To achieve lockstep we
        process the elements counter to instruction flow direction.
        """
        super().tick(cntr)

        self._retired_instructions.clear()

        for sb in self._rf_scoreboards.values():
            sb.tick(cntr)

        for ps in self._pipes.values():
            for p in ps:
                p.tick(cntr)
                self._retired_instructions.extend(p.retired_instrs)

        if self._branch_prediction == "none":
            for instr in self._retired_instructions:
                if instr.is_branch:
                    self._sched_unit.branch_resolved()
                    self._fetch_unit.branch_resolved()
                    break

        for dq in self._sched_unit.queues:
            while dq:
                if self.dispatch_instruction(dq[0], cntr):
                    dq.popleft()
                else:
                    break

        # Update retired instruction count.
        cntr.retired_instruction_count += len(self._retired_instructions)

    # Implements interfaces.ExecUnit
    def tock(self, cntr: Counter) -> None:
        super().tock(cntr)


        self._retired_instructions.clear()

        for sb in self._rf_scoreboards.values():
            sb.tock(cntr)

        for ps in self._pipes.values():
            for p in ps:
                p.tock(cntr)
                self._retired_instructions.extend(p.retired_instrs)

        # Update retired instruction count.
        cntr.retired_instruction_count += len(self._retired_instructions)

    def dispatch_instruction(self, instr: Instruction, cntr: Counter):
        # TODO(sflur): use other policies to choose pipe, instead of the first
        # free one.
        kind = self.get_functional_unit(instr)
        for pipe in self._pipes[kind]:
            if pipe.try_dispatch(instr, cntr):
                return True

        return False

    # Implements interfaces.ExecUnit
    def print_state_detailed(self, file) -> None:
        for pipes in self._pipes.values():
            for pipe in pipes:
                pipe.print_state_detailed(file)

        print("[re] " + ", ".join(str(i) for i in self._retired_instructions),
              file=file)

    # Implements interfaces.ExecUnit
    def get_state_three_valued_header(self) -> Sequence[str]:
        return [pipe.get_state_three_valued_header()
                for pipes in self._pipes.values() for pipe in pipes]

    # Implements interfaces.ExecUnit
    def get_state_three_valued(self, vals: Sequence[str]) -> Sequence[str]:
        return [pipe.get_state_three_valued(vals)
                for pipes in self._pipes.values() for pipe in pipes]
