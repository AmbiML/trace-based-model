#! /usr/bin/env python3

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
