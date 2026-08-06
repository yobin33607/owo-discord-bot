#!/usr/bin/env python3
"""Generate the Limey Discord Theme extension icons (no dependencies).

Draws a dark rounded square with a red "L" at 16/32/48/128 px and writes
valid PNGs using only the standard library (zlib + struct).
Run from this directory:  python3 generate_icons.py
"""
import os
import struct
import zlib

BG = (15, 15, 18)    # dashboard background
RED = (255, 62, 62)  # dashboard primary


def _chunk(tag, data):
    out = struct.pack(">I", len(data)) + tag + data
    out += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return out


def render(size):
    radius = max(1, int(size * 0.16))
    bar_w = max(2, int(size * 0.26))
    x0 = int(size * 0.18)
    x1 = min(size - 1, x0 + bar_w)
    y_top = int(size * 0.14)
    y_bot = int(size * 0.92)
    yh0 = int(size * 0.62)
    yh1 = int(size * 0.80)
    x_end = int(size * 0.84)

    corners = ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1))
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            color = BG
            if x0 <= x <= x1 and y_top <= y <= y_bot:
                color = RED
            elif yh0 <= y <= yh1 and x0 <= x <= x_end:
                color = RED
            for cx, cy in corners:
                if (x - cx) ** 2 + (y - cy) ** 2 < radius ** 2:
                    color = BG
                    break
            row += bytes(color)
        rows.append(b"\x00" + bytes(row))  # filter byte 0 = None

    raw = b"".join(rows)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )
    return png


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    for size in (16, 32, 48, 128):
        path = os.path.join(here, f"icon{size}.png")
        with open(path, "wb") as f:
            f.write(render(size))
        print(f"wrote {path} ({os.path.getsize(path)} bytes)")


if __name__ == "__main__":
    main()
