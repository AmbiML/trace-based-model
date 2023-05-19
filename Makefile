include common.mk

.DEFAULT_GOAL := all
all: $(OUT_PACKAGES)/FBInstruction/Instruction.py

# flatc actually generates two files, Instruction.py and Instructions.py. make
# 4.3 supports `&:` for multiple targets, but it's probably not wise to depend
# on such a new version of make, so instead we use Instruction.py as a sentinel
# for both files.
$(OUT_PACKAGES)/FBInstruction/Instruction.py: config/instruction.fbs | $(OUT_PACKAGES)
	$(FLATC) -o $(OUT_PACKAGES) --python $<

$(OUT_PACKAGES):
	mkdir -p $@

ifneq "$(wildcard $(ROOTDIR)/toolchain/riscv-opcodes)" ""
# Regenerate the pipe-maps for RISC-V, based on the opcodes from the
# riscv-opcodes repo. The associated pipes are copied from the old json file.
RISCV_EXTS := $(shell find $(ROOTDIR)/toolchain/riscv-opcodes -maxdepth 1 -name 'opcodes-*' -printf '%f\n' | cut -d- -f2-)
riscv_pipe_maps: $(RISCV_EXTS:%=pipe_maps/riscv/%.json)
.PHONY: riscv_pipe_maps
endif

pipe_maps/riscv/%.json: $(ROOTDIR)/toolchain/riscv-opcodes/opcodes-%
	$(PYTHON) $(IMPORT_RISCV_OPCODES) $(if $(wildcard $@),-m $@) -n $@.new $<
	mv $@.new $@


upgrade-requirements.txt:
	pip-compile --generate-hashes --upgrade requirements.in
.PHONY: requirements.txt

# Use pylint 2.13.9
lint:
	$(PYLINT) tbm/*.py
.PHONY:lint

type-check:
	$(PYTYPE) tbm/tbm.py tbm/gentrace-spike.py tbm/merge-counters.py
.PHONY: type-check

# After running pytype you can merge the inferred types into the .py files.
# `make merge-pyi` will merge to all files.
# `make merge-pyi-<module>` will merge only to module.
define merge-pyi
merge-pyi-$(1):
	$(MERGE_PYI) -i $(2) $(3)
.PHONY: merge-pyi-$(1)
merge-pyi: merge-pyi-$(1)
endef
.PHONY: merge-pyi

$(foreach py,$(wildcard tbm/*.py),\
	$(eval name = $(basename $(notdir $(py))))\
	$(eval pyi = .pytype/pyi/$(name).pyi)\
	$(if $(wildcard $(pyi)),\
		$(eval $(call merge-pyi,$(name),$(py),$(pyi)))))

clean:
	$(RM) $(OUT_PACKAGES)/FBInstruction/Instruction.py
	$(RM) $(OUT_PACKAGES)/FBInstruction/Instructions.py
	$(RM) -r __pycache__
	$(RM) -r .pytype
.PHONY: clean
