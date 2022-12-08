""" Store command-line options for global access. """

import argparse
from typing import Sequence

args = None


def parse_args(argv: Sequence[str], description: str) -> None:
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument("-u",
                        "--uarch",
                        required=True,
                        help="Microarchitecture configuration file",
                        metavar="JSON")

    parser.add_argument("-e",
                        "--extend",
                        action="append",
                        default=[],
                        help="Extension used to modify microarchitecture. This"
                        " option can be used multiple times.",
                        metavar="JSON")

    parser.add_argument("-s",
                        "--set",
                        action="append",
                        default=[],
                        help="Modify individual parts of the microarchitecture."
                        " This option can be used multiple times.",
                        metavar="PATH=VALUE")

    parser.add_argument("-r",
                        "--report",
                        help="Print report to FILE (otherwise report is printed"
                        " to stdout)",
                        metavar="FILE")

    parser.add_argument("--report-dont-include-cfg",
                        action='store_true',
                        help="Don't include the configuration with the report.",
                        dest="report_dont_include_cfg")

    parser.add_argument("--save-counters",
                        help="Save counters to FILE for later processing",
                        metavar="FILE",
                        dest="save_counters")

    parser.add_argument("-t",
                        "--print-trace",
                        choices=["detailed", "three-valued"],
                        help="Print cycle-by-cycle trace",
                        dest="print_trace")

    parser.add_argument("--print-from-cycle",
                        default=0,
                        type=int,
                        help="Start printing only from cycle N",
                        metavar="N",
                        dest="print_from_cycle")

    parser.add_argument("--cycles",
                        type=int,
                        help="Stop running after N cycles",
                        metavar="N",
                        dest="print_cycles")

    parser.add_argument("--instructions",
                        default="0:",
                        help="Restrict the run to the instructions between N"
                        " and M",
                        metavar="N:[M]")

    parser.add_argument("--json-trace",
                        action='store_true',
                        help="Read the input trace as JSON",
                        dest="json_trace")

    parser.add_argument("--json-trace-buffer-size",
                        type=int,
                        default=100000,
                        help="For efficiency, read N instructions at a time.",
                        metavar="N",
                        dest="json_trace_buffer_size")

    # The -v flag is setup so that verbose holds the number of times the flag
    # was used. This is the standard way to use -v, even though at the moment
    # we have only two levels of verbosity: warning (the default, with no -v),
    # and info.
    parser.add_argument("-v",
                        "--verbose",
                        default=0,
                        action="count",
                        help="Increase the verbosity level. By default only"
                        " errors and warnings will show. Use '-v' to also show"
                        " information messages.")

    parser.add_argument("trace", nargs="?", help="Input trace", metavar="FILE")

    global args  # pylint: disable=global-statement
    args = parser.parse_args(argv)
