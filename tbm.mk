# Make rules for running TBM (and related tools)

include common.mk

FORCE:
.PHONY: FORCE

###############################################################################
## Run Spike to generate .spike functional traces

# The memory regions, and entry point can be found like this:
# <toolchain>/bin/riscv32-unknown-elf-readelf -l <elf-file>
SPIKE_MEM := 0x34000000:0x1000000
SPIKE_ENTRY := 0x34000000
SPIKE_OPTS += $(if $(SPIKE_MEM),-m$(SPIKE_MEM))
SPIKE_OPTS += $(if $(SPIKE_ENTRY),--pc=$(SPIKE_ENTRY))
SPIKE_OPTS += --varch=vlen:512,elen:32
SPIKE_OPTS += -l --log-commits

# The first prerequisite must be the ELF file.
# CYCLES can be a number, in which case the trace will terminate after that
# many instructions have been executed.
%.spike:
	$(RM) $@ $@.tmp
	{ echo "run" $(CYCLES); echo "quit"; } > $@.cmd
	$(SPIKE) $(SPIKE_OPTS) -d --debug-cmd=$@.cmd --log=$@.tmp $<
	mv $@.tmp $@
# It would be more appropriate to use `.SECONDARY`, instead of `.PRECIOUS`, but
# only `.PRECIOUS` supports the `%` wildcard. To compensate for the difference
# we first write to a .tmp file and then mv it to the real target.
.PRECIOUS: %.spike

###############################################################################
## Run gentrace-spike.py to generate .trace elaborated functional traces

%.trace.json: GENTRACE_OPTS += --json
%.trace %.trace.json: %.spike
	$(RM) $@ $@.tmp
	$(PYTHON) $(GENTRACE) $(GENTRACE_OPTS) --outfile $@.tmp $<
	mv $@.tmp $@
# It would be more appropriate to use `.SECONDARY`, instead of `.PRECIOUS`, but
# only `.PRECIOUS` supports the `%` wildcard. To compensate for the difference
# we first write to a .tmp file and then mv it to the real target.
.PRECIOUS: %.trace

###############################################################################
## Run tbm.py to genertae .tbm_log reports

# This is a small hack to let you run tbm.py with --print-trace instead of
# --report. The trace is printed to stdout and the .tbm_log file is not
# touched. See 'tbm.py -h' for the possible values of TRACE (i.e. the values
# --print-trace accepts).
ifeq "$(TRACE)" ""
  REPORT += --report $@
else
  REPORT += --print-trace $(TRACE)
endif

# Use this target when you expect tbm to be able to handle the whole trace.
%.tbm_log: %.trace $(if $(TRACE),FORCE)
	$(PYTHON) $(TBM) --uarch $(UARCH) $(TBM_OPTS) $(REPORT) $<

# Use this macro to run tbm in multiple concurrent instances, each one handling
# a different segment of the trace. The result is less accurate than running a
# single instance, but can be much faster with 'make -j $(nproc)'.
# Usage: $(eval $(call tbm_merge_log,<OUT>,<I>,<N>,<UARCH>))
# this will generate a rule for <OUT>.tbm_merge_log, and rules for <N>
# .tbm_counters files, each one covering a range of <I> instructions (last one
# is open-ended).
# Example: $(eval $(call tbm_merge_log,$(OUT)/test,400,3,uarch.json))
define tbm_merge_log
_RANGE := $$(shell for ((i = 0; i < $(3) - 1; ++i)); do echo "$$$$((i * $(2))):$$$$(((i + 1) * $(2)))"; done; echo "$$$$((i * $(2))):")
$$(foreach r,$$(_RANGE),$$(eval $$(call _tbm_merge_log,$(1),$$(r),$(4))))
endef

define _tbm_merge_log
_RANGE := $$(subst :, ,$(2))
_START := $$(word 1,$$(_RANGE))
_END := $$(word 2,$$(_RANGE))
$$(eval $$(call tbm_counters,$(1),$$(_START),$$(_END),$(3)))
endef

# Don't call directly, see the tbm_merge_log macro above.
%.tbm_merge_log:
	$(PYTHON) $(MERGE_COUNTERS) --report $@ $^

# Don't call directly, see the tbm_merge_log macro above.
# Usage: $(eval $(call tbm_counters,<OUT>,<N>,<M>,<UARCH>)).
# this will generate a rule for <OUT>.<N>_<M>.tbm_counters, which covers the
# instruction range <N> to <M>.
# Example: $(eval $(call tbm_merge_log,$(OUT)/test,0,10000,default_arch.json))
define tbm_counters
$(1).$(2)_$(3).tbm_counters: $(1).trace $4
	$$(PYTHON) $$(TBM) --uarch $(4) --save-counters $$@ --instructions "$(2):$(3)" $$(TBM_OPTS) $$<

$(1).tbm_merge_log: $(1).$(2)_$(3).tbm_counters
endef
