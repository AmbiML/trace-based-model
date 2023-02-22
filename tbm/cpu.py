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

"""CPU module."""

import collections
import itertools
import logging
import pickle
import sys
from typing import Any, Dict

from counter import Counter
from exec_unit import ExecUnit
from fetch_unit import FetchUnit
from functional_trace import FunctionalTrace
from memory_system import MemorySystem
from sched_unit import SchedUnit
import tbm_options
import utilities


logger = logging.getLogger(__name__)


class CPU:
    """Top level core model."""

    def __init__(self, pipe_map: Dict[str, str], rf_scoreboards: Dict[str, Any],
                 mem_sys: MemorySystem, config: Dict[str, Any],
                 trace: FunctionalTrace) -> None:
        """Construct a CPU object."""

        self._print_header_cycle = None

        # conunters
        self.counter = Counter()

        # Units
        self.fetch_unit = FetchUnit(config, trace)
        self.sched_unit = SchedUnit(config)
        self.exec_unit = ExecUnit(config, pipe_map, rf_scoreboards)
        self.mem_sys = mem_sys

        # Connect the units to each other.
        self.sched_unit.connect(self.fetch_unit,
                                self.exec_unit)
        self.exec_unit.connect(self.fetch_unit,
                               self.sched_unit)

        # The order of this list is significant, this is the order in which the
        # tick/tock phases will be executed, and different order will give
        # different results. Units that work in lockstep should be listed in
        # order that is counter to instruction flow order.
        self.units = [
            self.mem_sys,
            self.exec_unit,
            self.sched_unit,
            self.fetch_unit,
        ]

    def log(self, message: str) -> None:
        if tbm_options.args.print_from_cycle <= self.counter.cycles:
            logger.info("[CPU:%d] %s", self.counter.cycles, message)

    def simulate(self) -> None:
        """Run the simulation."""

        # For debugging! If self.counter.retired_instruction_count doesn't
        # change for deadlock_threshold cycles, we suspect TBM is in a
        # deadlock, and terminate the execution.
        prev_ret_insts = 0
        maybe_deadlock_count = 0
        deadlock_threshold = 100

        for unit in self.units:
            unit.reset(self.counter)

        with utilities.CallEvery(30,
                lambda: logger.info("%s retired instructions",
                                    self.counter.retired_instruction_count)):
            # simulation's main loop
            while (not self.fetch_unit.eof() or
                   any(u.pending() for u in self.units)):

                if (tbm_options.args.print_cycles is not None and
                    self.counter.cycles >= tbm_options.args.print_cycles):
                    break

                self.counter.cycles += 1

                self.log("start tick")
                for unit in self.units:
                    unit.tick(self.counter)

                self.log("start tock")
                for unit in self.units:
                    unit.tock(self.counter)

                if tbm_options.args.print_trace:
                    self.print_state(tbm_options.args.print_trace)

                # Stop the simulation if we suspect a deadlock.
                if prev_ret_insts == self.counter.retired_instruction_count:
                    maybe_deadlock_count += 1
                    if maybe_deadlock_count > deadlock_threshold:
                        self.print_state_detailed(file=sys.stderr)
                        logger.error("(cycle %d) retired instruction count has"
                                     " not changed for %d cycles, this is"
                                     " probably a TBM bug.",
                                     self.counter.cycles, deadlock_threshold)
                        sys.exit(1)
                else:
                    prev_ret_insts = self.counter.retired_instruction_count
                    maybe_deadlock_count = 0

        if tbm_options.args.save_counters:
            with open(tbm_options.args.save_counters, "wb") as out:
                pickle.dump(self.counter, out, pickle.HIGHEST_PROTOCOL)

        if tbm_options.args.report:
            # Save report to file
            with open(tbm_options.args.report,
                      "w" if tbm_options.args.report_dont_include_cfg else "a",
                      encoding="ascii") as out:
                self.print_report(out)
        else:
            # Or print report to stdout
            self.print_report()

    def print_report(self, file=sys.stdout) -> None:
        self.counter.print(file)

        for unit in self.units:
            pending = unit.pending()
            if pending:
                print(f"*** Warning: pending instructions in {unit.name}:"
                      f" {pending}", file=file)

    def print_state(self, print_trace: str, file=sys.stdout) -> None:
        """Dump the current snapshot."""
        if not tbm_options.args.print_from_cycle <= self.counter.cycles:
            return

        if print_trace == "detailed":
            self.print_state_detailed(file=file)
        else:
            assert print_trace == "three-valued"
            self.print_state_three_valued(file=file)

    def print_state_detailed(self, file=sys.stdout) -> None:
        """Dump a detailed snapshot."""
        print(file=file)
        for unit in self.units:
            unit.print_state_detailed(file)

    def print_state_three_valued(self, file=sys.stdout) -> None:
        """Dump a three-valued snapshot."""

        pp_vals = ["-", "P", "F"]

        values = collections.deque([str(self.counter.cycles)])
        for unit in self.units:
            values.extend(unit.get_state_three_valued(pp_vals))

        # Print the header lines the first time we get here, and then every 100
        # cycles.
        if self._print_header_cycle is None:
            # Record the remainder the first time we print a line, and then
            # print the header everytime we see it.
            self._print_header_cycle = self.counter.cycles % 100

        if self._print_header_cycle == self.counter.cycles % 100:
            headers = collections.deque(["cycle"])
            for unit in self.units:
                headers.extend(unit.get_state_three_valued_header())

            # Transpose the headers (i.e. print them vertically)
            height = max(len(h) for h in headers)
            lines = [collections.deque() for _ in range(height)]
            for header, val in zip(headers, values):
                # Because lines was constructed to match the longest header we
                # know that in the zip_longest below it's the header that will
                # be filled with fillvalue to match lines' length.
                assert len(header) <= len(lines)
                for c, line in itertools.zip_longest(reversed(header),
                                                     lines,
                                                     fillvalue=" "):
                    line.append(f"{c:{len(val)}}")

            print(file=file)
            for line in reversed(lines):
                print("|".join(line), file=file)
            print("+".join("-" * len(val) for val in values), file=file)

        print("|".join(values), file=file)
