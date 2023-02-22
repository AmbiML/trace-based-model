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


"""Trace based model.

Models the microarchitectural behavior of a processor
based on a trace obtained from a functional simulator.
"""

import json
import logging
from typing import Any, Dict, Sequence
import sys

import jsonschema
import yaml

from cpu import CPU
from functional_trace import FunctionalTrace
from memory_system import MemorySystem
from scalar_pipe import ScalarPipe
import scoreboard
import tbm_options
import utilities
from vector_pipe import VectorPipe


logger = logging.getLogger("tbm")


def schema_validator() -> jsonschema.protocols.Validator:
    # TODO(b/261619078): use importlib instead of relative path
    schema_file_name = "config/uarch.schema.json"

    with open(schema_file_name, "r", encoding="ascii") as schema_file:
        uarch_schema = json.load(schema_file)

    # Check that the schema is valid
    vcls = jsonschema.validators.validator_for(uarch_schema)
    try:
        vcls.check_schema(uarch_schema)
    except jsonschema.exceptions.SchemaError as e:
        logger.error("in '%s':\n%s", schema_file_name, e.message)
        sys.exit(1)

    return vcls(uarch_schema)


def validate_uarch(validator: jsonschema.protocols.Validator,
                   uarch: Dict[str, Any]):
    errors = sorted(validator.iter_errors(uarch), key=lambda e: e.path)
    if errors:
        errs = []
        for err in errors:
            if err.path:
                errs.append("/".join(str(p) for p in err.path) + ": "
                            + err.message)
            else:
                errs.append(err.message)
        logger.error("Found %d errors in the microarchitecture"
                     " configuration:\n%s",
                     len(errors), "\n".join(errs))
        sys.exit(1)


def load_config_file(name: str) -> Dict[str, Any]:
    with open(name, "r", encoding="ascii") as file:
        if name.endswith(".json"):
            return json.load(file)

        if not name.endswith(".yaml"):
            logger.warning("The file '%s' has an unrecognized suffix (expected"
                           " .json or .yaml). Trying to load it as YAML.",
                           name)

        return yaml.safe_load(file)


def load_uarch() -> Dict[str, Any]:
    """Read micro-architecture description."""

    # Read in the micro-architecture description.
    uarch_desc = load_config_file(tbm_options.args.uarch)

    # Read in the micro-architecture description schema.
    validator = schema_validator()

    # Check that the original (un-patched) uarch is valid.
    validate_uarch(validator, uarch_desc)

    # Read additional modifications from files.
    for fl in tbm_options.args.extend:
        logger.info("Applying modifications from file '%s'", fl)

        data = load_config_file(fl)

        merge_config(uarch_desc, data)

    # Apply command line modifications.
    for cl_set in tbm_options.args.set:
        logger.info("Applying setting '%s'", cl_set)

        path, value = cl_set.split("=")
        path = path.split(".")

        apply_setting(uarch_desc, path, json.loads(value))

    # Check that the patched uarch is valid
    if tbm_options.args.extend or tbm_options.args.set:
        validate_uarch(validator, uarch_desc)

    remove_comments(uarch_desc)

    if not tbm_options.args.report_dont_include_cfg:
        if tbm_options.args.report:
            # Save to file
            with open(tbm_options.args.report, "w", encoding="ascii") as out:
                print("Configuration:", file=out)
                print(json.dumps(uarch_desc, indent=2), file=out)
                print(file=out)
        else:
            # Or print to stdout
            print("Configuration:")
            print(json.dumps(uarch_desc, indent=2))
            print()

    return uarch_desc


def apply_setting(uarch: Dict[str, Any], path: Sequence[str],
                  value: Any) -> None:
    """Modify an element of micro-architectural description.

    Args:
      uarch: uArch configuration to be modified
      path: path through tree of dictionaries
      value: value to be set
    """
    # We expect path to be non-empty
    assert path

    for idx, seg in enumerate(path):
        if seg not in uarch:
            logger.error("attempt to override non-existent element: %s",
                         ".".join(path[:idx+1]))
            sys.exit(1)

        # Traverse down `path` to update uarch until the last element.
        if idx < len(path) - 1:
            uarch = uarch[seg]
        else:
            # Last element
            logger.info("Changing '%s' to '%s'", seg, value)
            uarch[seg] = value


def merge_config(uarch: Dict[str, Any],
                 modification: Dict[str, Any]) -> None:
    """Merge modification tree into uarch description.

    Modification is performed by recursing down to leaves
    replacing old entries with entries from modification.

    Args:
      uarch: uArch configuration to be modified
      modification: configuration to be merged into uarch
    """

    for key, val in modification.items():
        if (key in uarch and isinstance(val, dict) and not val.pop("replace",
                                                                   False)):
            merge_config(uarch[key], val)
        else:
            logger.info("  Replacing '%s' with '%s'", key, val)
            uarch[key] = val


def remove_comments(desc: Dict[str, Any]) -> None:
    comments = [
        k for k in desc if k == "description" or k.startswith("__comment__")
    ]

    for k in comments:
        del desc[k]

    for _, val in desc.items():
        if isinstance(val, dict):
            remove_comments(val)


def create_scoreboard(uid: str, desc: Dict[str, Any],
                      config_desc: Dict[str, Any]):
    if desc["type"] == "scalar":
        return scoreboard.Preemptive(uid, desc)

    if desc["type"] == "vector":
        return scoreboard.VecPreemptive(uid, desc, config_desc["vector_slices"])

    assert False


def create_cpu(uarch_desc: Dict[str, Any], in_trace: FunctionalTrace) -> CPU:
    """Create CPU."""

    # Read in the pipe maps.
    pipe_map = {}
    pipe_map_keys = pipe_map.keys()  # This is a dynamic view
    for pm_file in uarch_desc["pipe_maps"]:
        with open(pm_file, "r", encoding="ascii") as pm_io:
            pm = json.load(pm_io)

        pm.pop("__comment__", None)

        if pipe_map_keys & pm.keys():
            logger.error("instruction(s) with multiple mappings: %s",
                   ", ".join(pipe_map.keys() & pm.keys()))
            sys.exit(1)

        pipe_map.update(pm)

    pipe_map = {k: v for k, v in pipe_map.items() if v != "UNKNOWN"}

    rf_scoreboards = {
        uid: create_scoreboard(uid, rf_desc, uarch_desc["config"])
        for uid, rf_desc in uarch_desc["register_files"].items()
    }

    mem_sys = MemorySystem(uarch_desc["memory_system"])

    cpu = CPU(pipe_map, rf_scoreboards, mem_sys, uarch_desc["config"], in_trace)

    for uid, iq_desc in uarch_desc["issue_queues"].items():
        cpu.sched_unit.add_queue(uid, iq_desc)

    for kind, fu_desc in uarch_desc["functional_units"].items():
        if fu_desc["type"] == "scalar":
            cpu.exec_unit.add_pipe(kind,
                [ScalarPipe(f"{kind}{i}", kind, fu_desc, cpu.mem_sys,
                            rf_scoreboards)
                 for i in range(fu_desc.get("count", 1))])
        else:
            assert fu_desc["type"] == "vector"

            cpu.exec_unit.add_pipe(kind,
                [VectorPipe(f"{kind}{i}", kind, fu_desc,
                            uarch_desc["config"]["vector_slices"], cpu.mem_sys,
                            rf_scoreboards)
                 for i in range(fu_desc.get("count", 1))])

    return cpu


def main(argv: Sequence[str]) -> int:
    tbm_options.parse_args(argv, description=__doc__)
    # This assert convinces pytype that args is not None.
    assert tbm_options.args is not None

    log_level = logging.WARNING
    if tbm_options.args.verbose > 0:
        log_level = logging.INFO

    utilities.logging_config(log_level)

    uarch = load_uarch()

    if tbm_options.args.trace is None:
        tr = FunctionalTrace.from_json(sys.stdin, tbm_options.args.instructions)
        cpu = create_cpu(uarch, tr)
        cpu.simulate()
        return 0

    if tbm_options.args.json_trace:
        with open(tbm_options.args.trace, "r", encoding="ascii") as in_trace:
            tr = FunctionalTrace.from_json(in_trace,
                                           tbm_options.args.instructions)
            cpu = create_cpu(uarch, tr)
            cpu.simulate()
            return 0

    with open(tbm_options.args.trace, "rb") as in_trace:
        tr = FunctionalTrace.from_fb(in_trace, tbm_options.args.instructions)
        cpu = create_cpu(uarch, tr)
        cpu.simulate()
        return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
