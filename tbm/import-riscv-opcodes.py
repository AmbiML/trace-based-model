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

"""Generate a pipe-map json file from RISC-V opcodes file.

Generate a new pipe-map json file, with all the opcodes from OPFILE. OPFILE is
a file from the riscv-opcodes repo. Entries are created in the new map file
only for opcodes from the OPFILE file. If an opcode also has an entry in the
old map file, the pipe for this opcode will be copied to the new map file,
otherwise it will be set to "UNKNOWN".
"""

import argparse
import json
import logging
import sys
from typing import Sequence


import utilities


logger = logging.getLogger(__name__)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument("-m", "--oldmap",
                        help="The old pipe-map file",
                        metavar="JSON")

    parser.add_argument("-n", "--newmap",
                        required=True,
                        help="Output pipe-map file",
                        metavar="JSON")

    # The -v flag is setup so that verbose holds the number of times the flag
    # was used. This is the standard way to use -v, even though at the moment
    # we have only two levels of verbosity: warning (the default, with no -v),
    # and info.
    parser.add_argument("-v", "--verbose",
                        default=0,
                        action="count",
                        help="Increase the verbosity level. By default only"
                        " errors and warnings will show. Use '-v' to also show"
                        " information messages.")

    parser.add_argument("opcodes_file",
                        help="Opcode file",
                        metavar="OPFILE")

    args = parser.parse_args(argv)

    log_level = logging.WARNING
    if args.verbose > 0:
        log_level = logging.INFO

    utilities.logging_config(log_level)

    if args.oldmap:
        with open(args.oldmap, "r", encoding="ascii") as oldmap_io:
            old_map = json.load(oldmap_io)
    else:
        old_map = {}

    new_map = {
        "__comment__":
        "AUTO-GENERATED FILE, DO NOT ADD NEW KEYS (it's ok to change values)!"
    }

    with open(args.opcodes_file, "r", encoding="ascii") as opcodes_io:
        for line in opcodes_io:
            line = line.partition("#")
            tokens = line[0].split()

            if not tokens:
                continue

            name = tokens[0]
            pseudo = name[0] == "@"
            if pseudo:
                name = name[1:]

            if name in old_map:
                new_map[name] = old_map[name]
            else:
                logger.info("Adding a new opcode: '%s':  'UNKNOWN'.", name)
                new_map[name] = "UNKNOWN"

    with open(args.newmap, "w", encoding="ascii") as newmap_io:
        json.dump(new_map, newmap_io, indent=2)
        newmap_io.write("\n")

    old_keys = old_map.keys() - new_map.keys()
    if old_keys:
        logger.warning("Some opcodes from the old map were dropped (%d): %s",
                       len(old_keys),
                       ", ".join(old_keys))

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
