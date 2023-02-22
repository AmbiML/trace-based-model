#! /usr/bin/env python3
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


"""Merge multiple saved counters to one report."""


import argparse
import pickle
import sys
from typing import Sequence


from counter import Counter


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("-r", "--report",
                        help="Print report to RFILE",
                        metavar="RFILE")

    parser.add_argument("files",
                        nargs="+",
                        help="TBM counter files",
                        metavar="FILE")

    args = parser.parse_args(argv)

    data = None

    for cfile in args.files:
        with open(cfile, "rb") as ocfile:
            new: Counter = pickle.load(ocfile)

        if data is None:
            data = new
        else:
            data += new

    assert data

    if args.report:
        with open(args.report, "w", encoding="ascii") as out:
            data.print(out)
    else:
        data.print()

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
