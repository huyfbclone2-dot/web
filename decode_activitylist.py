import argparse
import zlib
from pathlib import Path


# Extracted from LobbyPreloader native .rodata (L__2E_str324).
KEY = b"3.1415"
BLOCK_SIZE = 1024
PATCH_OFFSET = 91


def decode_activity_bytes(data: bytes) -> bytes:
    patched = bytearray(data)
    block_count = (len(patched) + BLOCK_SIZE - 1) // BLOCK_SIZE

    for block_index in range(block_count):
        pos = block_index * BLOCK_SIZE + PATCH_OFFSET
        if pos >= len(patched):
            break

        key_index = 5 - (block_index % 6)
        patched[pos] ^= KEY[key_index]

    return zlib.decompress(bytes(patched))


def main():
    parser = argparse.ArgumentParser(description="Decode activitylist XML encrypted by LobbyPreloader.")
    parser.add_argument("input", type=Path, help="Encrypted activity bin path")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("activitylist.xml"),
        help="Decoded XML output path",
    )
    args = parser.parse_args()

    decoded = decode_activity_bytes(args.input.read_bytes())
    args.output.write_bytes(decoded)

    print(f"[ok] Wrote decoded XML: {args.output.resolve()}")
    print(f"[info] Output size: {len(decoded)} bytes")


if __name__ == "__main__":
    main()
