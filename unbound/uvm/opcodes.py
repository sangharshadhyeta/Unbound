"""
UVM Instruction Set — all opcodes are plain integers.
A miner receiving a number stream cannot distinguish opcodes from data values
without the schema, which only the submitter holds.
"""

# Stack operations
PUSH  = 1   # PUSH <value>        → push literal onto stack
POP   = 2   # POP                 → discard top of stack
DUP   = 3   # DUP                 → duplicate top of stack
SWAP  = 4   # SWAP                → swap top two stack values
LOAD  = 5   # LOAD <addr>         → push value from memory[addr]
STORE = 6   # STORE <addr>        → pop value, store to memory[addr]

# Arithmetic (integer)
ADD   = 10  # ADD                 → pop a, b; push a+b
SUB   = 11  # SUB                 → pop a, b; push a-b
MUL   = 12  # MUL                 → pop a, b; push a*b
DIV   = 13  # DIV                 → pop a, b; push a//b
MOD   = 14  # MOD                 → pop a, b; push a%b
NEG   = 15  # NEG                 → pop a; push -a

# Comparison (result: 1=true, 0=false)
EQ    = 20  # EQ                  → pop a, b; push 1 if a==b else 0
NEQ   = 21  # NEQ                 → pop a, b; push 1 if a!=b else 0
LT    = 22  # LT                  → pop a, b; push 1 if a<b else 0
LTE   = 23  # LTE                 → pop a, b; push 1 if a<=b else 0
GT    = 24  # GT                  → pop a, b; push 1 if a>b else 0
GTE   = 25  # GTE                 → pop a, b; push 1 if a>=b else 0

# Logic
AND   = 30  # AND                 → pop a, b; push a&b
OR    = 31  # OR                  → pop a, b; push a|b
NOT   = 32  # NOT                 → pop a; push ~a (bitwise)
XOR   = 33  # XOR                 → pop a, b; push a^b
SHL   = 34  # SHL                 → pop a, n; push a<<n
SHR   = 35  # SHR                 → pop a, n; push a>>n

# Control flow
JMP   = 40  # JMP <offset>        → unconditional jump by offset
JT    = 41  # JT  <offset>        → pop cond; jump if cond != 0
JF    = 42  # JF  <offset>        → pop cond; jump if cond == 0

# I/O
INPUT  = 50 # INPUT               → push next value from input buffer
OUTPUT = 51 # OUTPUT              → pop value, append to output buffer

# Floating point
FCONST = 60 # FCONST <bits64>     → push float (immediate = IEEE 754 double as int64)
FADD   = 61 # FADD                → pop a, b (floats); push a+b
FSUB   = 62 # FSUB                → pop a, b; push a-b
FMUL   = 63 # FMUL                → pop a, b; push a*b
FDIV   = 64 # FDIV                → pop a, b; push a/b
FMOD   = 65 # FMOD                → pop a, b; push fmod(a, b)
FNEG   = 66 # FNEG                → pop a; push -a
ITOF   = 67 # ITOF                → pop int; push float(int)
FTOI   = 68 # FTOI                → pop float; push int(float) (truncate toward zero)

# Misc
HALT  = 99  # HALT                → stop execution

# Human-readable names (for debugging schema — user side only)
OPCODE_NAMES = {
    PUSH: "PUSH", POP: "POP", DUP: "DUP", SWAP: "SWAP",
    LOAD: "LOAD", STORE: "STORE",
    ADD: "ADD", SUB: "SUB", MUL: "MUL", DIV: "DIV",
    MOD: "MOD", NEG: "NEG",
    EQ: "EQ", NEQ: "NEQ", LT: "LT", LTE: "LTE",
    GT: "GT", GTE: "GTE",
    AND: "AND", OR: "OR", NOT: "NOT", XOR: "XOR",
    SHL: "SHL", SHR: "SHR",
    JMP: "JMP", JT: "JT", JF: "JF",
    INPUT: "INPUT", OUTPUT: "OUTPUT",
    FCONST: "FCONST", FADD: "FADD", FSUB: "FSUB",
    FMUL: "FMUL", FDIV: "FDIV", FMOD: "FMOD", FNEG: "FNEG",
    ITOF: "ITOF", FTOI: "FTOI",
    HALT: "HALT",
}

# Opcodes that consume one immediate argument from the stream
HAS_IMMEDIATE = {PUSH, LOAD, STORE, JMP, JT, JF, FCONST}
