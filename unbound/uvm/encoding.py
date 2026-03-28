"""
UVM Binary Stream Encoding — Variable-Length (LEB128)

Same scheme as WebAssembly: every integer is encoded in the minimum
number of bytes it actually needs. Ones and zeroes only, no padding.

LEB128 (Little Endian Base 128):
  - Each byte carries 7 bits of data in its low bits
  - The high bit (MSB) is a continuation flag: 1 = more bytes follow, 0 = last byte
  - Opcodes use unsigned LEB128  (always ≤ 99, so always 1 byte)
  - Immediates use signed LEB128 (handles negative jump offsets)

Byte cost per value:
  value range          bytes
  ──────────────────   ─────
       0 …      127     1      (loop counters, small addresses, opcodes)
    -64 …       63      1      (short jump offsets, small negatives)
    128 …    16 383     2
  -8192 …    8 191      2
      …              3, 4, 5   (arbitrary precision, but rare in typical programs)

Comparison for a typical program (mostly small ints):
  JSON    ~3–5 bytes / integer  (ASCII digits + punctuation)
  Fixed   ~2.5 bytes / integer  (1 opcode + 4 immediate, averaged)
  LEB128  ~1–2 bytes / integer  (approaches information minimum)
"""

from typing import List
from .opcodes import HAS_IMMEDIATE


# ── Unsigned LEB128 ───────────────────────────────────────────────────────────

def _encode_uleb128(value: int, buf: bytearray):
    """Append unsigned LEB128 encoding of value to buf."""
    if value < 0:
        raise ValueError(f"uleb128 requires non-negative value, got {value}")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            buf.append(byte | 0x80)  # more bytes follow
        else:
            buf.append(byte)          # last byte
            break


def _decode_uleb128(data: bytes, pos: int) -> tuple[int, int]:
    """Decode unsigned LEB128 from data at pos. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError(f"Truncated uleb128 at byte {pos}")
        byte = data[pos]; pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not (byte & 0x80):
            return result, pos


# ── Signed LEB128 ─────────────────────────────────────────────────────────────

def _encode_sleb128(value: int, buf: bytearray):
    """Append signed LEB128 encoding of value to buf."""
    more = True
    while more:
        byte = value & 0x7F
        value >>= 7
        # Arithmetic shift: for negative numbers, check sign extension
        if (value == 0 and not (byte & 0x40)) or (value == -1 and (byte & 0x40)):
            more = False
        else:
            byte |= 0x80
        buf.append(byte)


def _decode_sleb128(data: bytes, pos: int) -> tuple[int, int]:
    """Decode signed LEB128 from data at pos. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError(f"Truncated sleb128 at byte {pos}")
        byte = data[pos]; pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not (byte & 0x80):
            # Sign-extend if the sign bit of the last group is set
            if byte & 0x40:
                result |= -(1 << shift)
            return result, pos


# ── Public API ────────────────────────────────────────────────────────────────

def encode(stream: List[int]) -> bytes:
    """
    Pack a UVM integer stream to variable-length bytes.
    Opcodes: unsigned LEB128. Immediates: signed LEB128.
    """
    buf = bytearray()
    i = 0
    n = len(stream)
    while i < n:
        op = stream[i]; i += 1
        _encode_uleb128(op, buf)
        if op in HAS_IMMEDIATE:
            if i >= n:
                raise ValueError(f"Stream truncated: opcode {op} expects immediate at pos {i}")
            _encode_sleb128(stream[i], buf); i += 1
    return bytes(buf)


def decode(data: bytes) -> List[int]:
    """Unpack variable-length bytes back to a UVM integer stream."""
    stream: List[int] = []
    pos = 0
    n = len(data)
    while pos < n:
        op, pos = _decode_uleb128(data, pos)
        stream.append(op)
        if op in HAS_IMMEDIATE:
            imm, pos = _decode_sleb128(data, pos)
            stream.append(imm)
    return stream


def size_report(stream: List[int]) -> dict:
    """Compare JSON, fixed-binary, and LEB128 sizes for a stream."""
    import json
    import struct

    json_bytes = len(json.dumps(stream, separators=(",", ":")).encode())
    leb_bytes  = len(encode(stream))

    # Fixed encoding: 1 byte opcode + 4 bytes immediate where applicable
    fixed_bytes = sum(
        5 if (i > 0 and stream[i-1] in HAS_IMMEDIATE) else 1
        for i in range(len(stream))
    )

    return {
        "integers":     len(stream),
        "json_bytes":   json_bytes,
        "fixed_bytes":  fixed_bytes,
        "leb128_bytes": leb_bytes,
        "vs_json":      round(json_bytes  / leb_bytes, 2),
        "vs_fixed":     round(fixed_bytes / leb_bytes, 2),
    }
