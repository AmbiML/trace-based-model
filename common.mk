ifndef ROOTDIR
$(error "ROOTDIR not defined! Did you forget to run 'source build/setup.sh' in the repo root?")
endif

OUT_PACKAGES := $(OUT)/tbm/packages
export PYTHONPATH := $(OUT_PACKAGES):$(PYTHONPATH)

OUT_TRACES := $(OUT)/tbm/traces

GENTRACE := tbm/gentrace-spike.py
IMPORT_RISCV_OPCODES := tbm/import-riscv-opcodes.py
MERGE_COUNTERS := tbm/merge-counters.py
TBM := tbm/tbm.py

FLATC := flatc
MERGE_PYI := merge-pyi
PYLINT := pylint
PYTHON := python3
PYTYPE := pytype
SPIKE := $(OUT)/host/spike/bin/spike

UARCH := config/rvv-simple.yaml
