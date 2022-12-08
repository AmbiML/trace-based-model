import re
from typing import Optional, Sequence, Tuple

# Precompile some regular expressions
RE_RVV_STORE = re.compile(r"(vse|vsuxei|vsse|vsoxei)\d+")
RE_ADDR_OFF1 = re.compile(r"^\d*\((\w+)\)$")
RE_ADDR_OFF2 = re.compile(r"^(\w+)\s*[-+]\s*(\d+|0x[0-9a-fA-F]+)$")
RE_IMM = re.compile(r"^(-?\d+|0x[0-9a-fA-F]+)$")


def asm_registers(
    mnemonic: str,
    operands: Sequence[str]) -> Tuple[Sequence[str], Sequence[str]]:
    """Generate list of inputs and output registers from operands.

    Args:
      mnemonic: assembly instruction mnemonic
      operands: operands

    Returns:
      inputs: input registers
      outputs: output registers

    TODO(sflur): The implementation is very incomplete. Instead of parsing the
    disassembled instruction, which is too much work, the functional simulator
    should provide the set of registers it read from (input) and wrote to
    (output), for every instruction. Spike only provides the register writes!
    Alterantively, use an external library to parse the instruction (machine
    code or disassembled).
    """

    if (mnemonic in ["sb", "sh", "sw", "sbu", "shu", "fsw", "fsd"] or
            RE_RVV_STORE.match(mnemonic)):
        # store
        input_ops = operands
        output_ops = []
    elif (mnemonic in ["j", "jr", "c.j"] or mnemonic.startswith("b")):
        # jump/branch
        input_ops = operands
        output_ops = []
    elif (mnemonic in ["jal", "jalr"] and len(operands) == 1):
        # pseudo-instructions
        input_ops = operands
        output_ops = ["x1"]
    else:
        # default behaviour: first operand is destination, remainder are outputs
        input_ops = operands[1:]
        output_ops = operands[:1]

    inputs = [r for r in [input_reg(o) for o in input_ops] if r]
    outputs = [o for o in output_ops if not o[0].isdigit()]

    # Add implicit inputs and outputs of instructions
    if mnemonic.startswith("vset"):
        outputs.extend(["vtype", "vl"])
    elif mnemonic.startswith("v"):
        inputs.extend(["vtype", "vl", "vstart"])

    return (normalize(inputs), normalize(outputs))


def input_reg(operand: str) -> Optional[str]:
    """Extract a register from an input operand.

    Discards immediate operands, branch address, etc.

    Args:
      operand: input operand

    Returns:
      register or None
    """

    # address offset
    m = RE_ADDR_OFF1.match(operand)
    if m:
        return m.group(1)

    # address offset
    m = RE_ADDR_OFF2.match(operand)
    if m:
        return m.group(1)

    # discard immediate operand
    if RE_IMM.match(operand):
        return None

    # default: it's probably a register
    return operand


# table of RISC-V ABI names
ABI_NAMES = {
    "zero": "x0",
    "ra": "x1",
    "sp": "x2",
    "gp": "x3",
    "tp": "x4",
    "t0": "x5",
    "t1": "x6",
    "t2": "x7",
    "s0": "x8",
    "s1": "x9",
    "a0": "x10",
    "a1": "x11",
    "a2": "x12",
    "a3": "x13",
    "a4": "x14",
    "a5": "x15",
    "a6": "x16",
    "a7": "x17",
    "s2": "x18",
    "s3": "x19",
    "s4": "x20",
    "s5": "x21",
    "s6": "x22",
    "s7": "x23",
    "s8": "x24",
    "s9": "x25",
    "s10": "x26",
    "s11": "x27",
    "t3": "x28",
    "t4": "x29",
    "t5": "x30",
    "t6": "x31",

    # This is the RVV mask register (not exactly abi).
    "v0.t": "v0",
}

# list of non-register names
BOGUS_REGISTERS = {
    "x0",
    "e8",
    "e16",
    "e32",
    "e64",
    "e128",
    "m1",
    "m2",
    "m4",
    "m8",
    "m16",
    "ta",
    "tu",
    "ma",
    "mu",
}


def normalize(rs: Sequence[str]) -> Sequence[str]:
    """Replace ABI register names with their architectural names (removing x0).

    Also, removes duplicates.

    Args:
      rs: list of registers.
    Returns:
      list of registers
    """
    return list({ABI_NAMES.get(r, r) for r in rs} - BOGUS_REGISTERS)


NOPS = {
    "nop",
    "c.nop",
    "fence",
    "fence.i",
    "sfence.vma",
    "wfi",
}


def is_nop(mnemonic):
    """Test whether an instruction mnemonic is a NOP in TBM sense.

    A NOP instruction is any instruction that TBM will retire without placing
    in a dispatch queue.
    """
    return mnemonic in NOPS


# List of all known branch instructions
BRANCHES = {
    "beq",
    "bne",
    "blt",
    "bge",
    "bltu",
    "bgeu",
    "jal",
    "jalr",
    "bnez",
    "beqz",
    "blez",
    "bgez",
    "bltz",
    "bgtz",
    "bleu",
    "bgtu",
    "j",
    "c.j",
    "jr",
    "ret",
    "sret",
    "mret",
    "ecall",
    "ebreak",
}


def is_branch(mnemonic: str) -> bool:
    """Test whether an instruction mnemonic is a branch."""
    return mnemonic in BRANCHES


FLUSHES = {
    "csrr",
    "csrw",
    "csrs",
    "csrwi",
    "csrrw",
    "csrrs",
    "csrrc",
    "csrrwi",
    "csrrsi",
    "csrrci",
    "fence",
    "fence.i",
    "sfence.vma",
}


def is_flush(mnemonic: str) -> bool:
    """Test whether an instruction mnemonic is a flush in TBM sense.

    A flush instruction is any instruction that should be placed in a dispatch
    queue (or retired, see is_nop) only when the pipeline is empty.
    """
    return mnemonic in FLUSHES


VCTRL = {
    "vsetivli",
    "vsetvli",
    "vsetvl",
}


def is_vctrl(mnemonic: str) -> bool:
    """Test whether an instruction mnemonic is a vctrl."""
    return mnemonic in VCTRL


# List of control/status registers (incomplete)
CSRS = {
    "cycle",
    "cycleh",
    "dcsr",
    "dpc",
    "dscratch0",
    "dscratch1",
    "fcsr",
    "fflags",
    "frm",
    "hcounteren",
    "hedeleg",
    "hgatp",
    "hgeie",
    "hgeip",
    "hideleg",
    "hie",
    "hip",
    "hstatus",
    "htimedelta",
    "htimedeltah",
    "htinst",
    "htval",
    "hvip",
    "instret",
    "instreth",
    "marchid",
    "mcause",
    "mcontext",
    "mcounteren",
    "mcountinhibit",
    "mcycle",
    "medeleg",
    "mepc",
    "mhartid",
    "mideleg",
    "mie",
    "mimpid",
    "minstret",
    "mintstatus",
    "mip",
    "misa",
    "mnxti",
    "mscratch",
    "mscratchcsw",
    "mscratchcswl",
    "mstatus",
    "mtinst",
    "mtval",
    "mtval2",
    "mtvec",
    "mtvt",
    "mvendorid",
    "pmpaddr0",
    "pmpaddr1",
    "pmpaddr10",
    "pmpaddr11",
    "pmpaddr12",
    "pmpaddr13",
    "pmpaddr14",
    "pmpaddr15",
    "pmpaddr2",
    "pmpaddr3",
    "pmpaddr4",
    "pmpaddr5",
    "pmpaddr6",
    "pmpaddr7",
    "pmpaddr8",
    "pmpaddr9",
    "pmpcfg0",
    "pmpcfg1",
    "pmpcfg2",
    "pmpcfg3",
    "satp",
    "scause",
    "scontext",
    "scounteren",
    "sedeleg",
    "sentropy",
    "sepc",
    "sideleg",
    "sie",
    "sintstatus",
    "sip",
    "snxti",
    "sscratch",
    "sscratchcsw",
    "sscratchcswl",
    "sstatus",
    "stval",
    "stvec",
    "stvt",
    "tcontrol",
    "tdata1",
    "tdata2",
    "tdata3",
    "time",
    "timeh",
    "tinfo",
    "tselect",
    "ucause",
    "uepc",
    "uie",
    "uintstatus",
    "uip",
    "unxti",
    "uscratch",
    "uscratchcsw",
    "uscratchcswl",
    "ustatus",
    "utval",
    "utvec",
    "utvt",
    "vcsr",
    "vl",
    "vlenb",
    "vsatp",
    "vscause",
    "vsepc",
    "vsie",
    "vsip",
    "vsscratch",
    "vsstatus",
    "vstart",
    "vstval",
    "vstvec",
    "vtype",
    "vxrm",
    "vxsat",
}
