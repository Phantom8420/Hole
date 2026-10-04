"""Draw the Hole app icon, desktop/assets/icon.png and icon.ico, with the standard library only.

An orange tile with the ring from the rail's Hole mark (shell/icons.js): the inner circle sits up and
to the right of the outer one, so the rim is thick at the bottom left and thin at the top right.
Every size is drawn from the geometry rather than scaled down, so the small ones stay crisp.

    python desktop/tools/make-icon.py
"""
import math
import os
import struct
import zlib

ORANGE = (0xF2, 0x6A, 0x2E)
INK = (0x1A, 0x0E, 0x07)
SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4

CORNER = 0.22  # all lengths are fractions of the icon's side
OUTER = 0.27
INNER = 0.168
INNER_OFFSET = (0.0485, -0.0425)


def sample(u, v):
    """The colour at (u, v), both 0..1, as (r, g, b, alpha)."""
    cx = min(max(u, CORNER), 1 - CORNER)
    cy = min(max(v, CORNER), 1 - CORNER)
    if (u - cx) ** 2 + (v - cy) ** 2 > CORNER * CORNER:
        return (0, 0, 0, 0)
    in_outer = math.hypot(u - 0.5, v - 0.5) <= OUTER
    in_inner = math.hypot(u - 0.5 - INNER_OFFSET[0], v - 0.5 - INNER_OFFSET[1]) < INNER
    return (*(INK if in_outer and not in_inner else ORANGE), 1)


def render(size):
    rows = []
    step = 1 / (size * SUPERSAMPLE)
    for y in range(size):
        row = bytearray([0])  # PNG filter type 0
        for x in range(size):
            r = g = b = a = 0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    cr, cg, cb, ca = sample((x * SUPERSAMPLE + sx + 0.5) * step, (y * SUPERSAMPLE + sy + 0.5) * step)
                    r, g, b, a = r + cr * ca, g + cg * ca, b + cb * ca, a + ca
            if a:
                row += bytes((round(r / a), round(g / a), round(b / a), round(255 * a / SUPERSAMPLE**2)))
            else:
                row += bytes(4)
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size, raw):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def ico(images):
    """images: {size: png bytes}. PNG-compressed entries, which Windows has read since Vista."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images.items():
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
    os.makedirs(out, exist_ok=True)
    images = {size: render(size) for size in SIZES}
    with open(os.path.join(out, "icon.png"), "wb") as f:
        f.write(images[256])
    with open(os.path.join(out, "icon.ico"), "wb") as f:
        f.write(ico(images))
    print("wrote", os.path.normpath(out), {size: len(data) for size, data in images.items()})


if __name__ == "__main__":
    main()
