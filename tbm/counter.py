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

"""Counter module."""

from __future__ import annotations

from dataclasses import dataclass, field
import sys
from typing import Optional


@dataclass(slots=True)
class Utilization:
    size: Optional[int] = None
    count: int = 0
    occupied: int = 0

    def utilization(self, cycles: int) -> float:
        if self.size is not None:
            return self.occupied * 100 / (cycles * self.size)

        return self.occupied * 100 / cycles

    # Overload +=
    def __iadd__(self, other: Utilization) -> Utilization:
        assert self.size == other.size

        self.count += other.count
        self.occupied += other.occupied

        return self


@dataclass(slots=True)
class Counter:
    cycles: int = 0

    retired_instruction_count: int = 0

    branch_count: int = 0

    stalls: dict[str, int] = field(default_factory=dict)

    utilizations: dict[str, Utilization] = field(default_factory=dict)

    scalar_load_store: int = 0
    scalar_load_store_stall: int = 0

    vector_load_store: int = 0
    vector_load_store_stall: int = 0

    # Overload +=
    def __iadd__(self, other: Counter) -> Counter:
        self.cycles += other.cycles

        self.retired_instruction_count += other.retired_instruction_count

        self.branch_count += other.branch_count

        # The assertion holds because the reset() functions assign 0 to all
        # keys.
        assert self.stalls.keys() == other.stalls.keys()
        for key, val in other.stalls.items():
            self.stalls[key] += val

        # The assertion holds because the reset() functions assign 0 to all
        # keys.
        for key, val in other.utilizations.items():
            self.utilizations[key] += val

        self.scalar_load_store += other.scalar_load_store
        self.scalar_load_store_stall += other.scalar_load_store_stall

        self.vector_load_store += other.vector_load_store
        self.vector_load_store_stall += other.vector_load_store_stall

        return self

    def print(self, file=sys.stdout) -> None:
        print(f"*** cycles: {self.cycles}", file=file)
        if self.cycles == 0:
            return

        # pylint: disable=consider-using-f-string
        print("*** retired instructions per cycle: %.2f (%d)" %
              (self.retired_instruction_count / self.cycles,
               self.retired_instruction_count),
              file=file)

        print("*** retired / fetched instructions: %.2f" %
              (self.retired_instruction_count / self.utilizations["FE"].count),
              file=file)

        print("*** branch count: " + str(self.branch_count), file=file)

        if self.scalar_load_store:
            print(
                "*** scalar load/store stall rate:"
                f" {self.scalar_load_store_stall / self.scalar_load_store:.2f}"
                " stalls per-instruction",
                file=file)

        if self.vector_load_store:
            print(
                "*** vector load/store stall rate:"
                f" {self.vector_load_store_stall / self.vector_load_store:.2f}"
                " stalls per-instruction",
                file=file)

        print(file=file)
        print("*** stall cycles:", file=file)
        for name, stall in self.stalls.items():
            val = stall * 100 // self.cycles
            print(f"  {name}: {val}% ({stall})", file=file)

        print(file=file)
        print("*** instructions per cycle:", file=file)
        for name, util in self.utilizations.items():
            val = util.count / self.cycles
            print(f"  {name}: {val:.2f} ({util.count})", file=file)

        print(file=file)
        print("*** utilization:", file=file)
        for name, util in self.utilizations.items():
            val = util.utilization(self.cycles)
            print(f"  {name}: {val:.0f}% ({util.count})", file=file)
