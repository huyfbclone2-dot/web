import argparse
import io
import struct
import zlib
from pathlib import Path

from PIL import Image


ACTIVITY_KEY = b"3.1415"
ACTIVITY_BLOCK_SIZE = 1024
ACTIVITY_PATCH_OFFSET = 91


def decode_activity_asset(data: bytes) -> bytes:
    patched = bytearray(data)
    for block_index in range((len(patched) + ACTIVITY_BLOCK_SIZE - 1) // ACTIVITY_BLOCK_SIZE):
        pos = block_index * ACTIVITY_BLOCK_SIZE + ACTIVITY_PATCH_OFFSET
        if pos >= len(patched):
            break
        patched[pos] ^= ACTIVITY_KEY[5 - (block_index % 6)]
    return bytes(patched)


def read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def ensure_swf_candidates(data: bytes) -> list[tuple[bytes, str]]:
    candidates: list[tuple[bytes, str]] = []
    signature = data[:3]
    if signature in (b"FWS", b"CWS", b"ZWS"):
        candidates.append((data, "plain"))

    decoded = decode_activity_asset(data)
    if decoded[:3] in (b"FWS", b"CWS", b"ZWS") and decoded != data:
        candidates.append((decoded, "activity-xor"))

    if not candidates:
        raise ValueError("Input is not a SWF and did not match the activity asset XOR format.")

    return candidates


def decompress_swf_body(data: bytes) -> tuple[int, int, bytes]:
    signature = data[:3]
    version = data[3]
    file_length = read_u32(data, 4)

    if signature == b"FWS":
        body = data[8:]
    elif signature == b"CWS":
        body = zlib.decompress(data[8:])
    elif signature == b"ZWS":
        import lzma

        prop = data[12]
        dict_size = read_u32(data, 13)
        lc = prop % 9
        rem = prop // 9
        lp = rem % 5
        pb = rem // 5
        filters = [
            {
                "id": lzma.FILTER_LZMA1,
                "dict_size": dict_size,
                "lc": lc,
                "lp": lp,
                "pb": pb,
            }
        ]
        body = lzma.decompress(data[17:], format=lzma.FORMAT_RAW, filters=filters)
    else:
        raise ValueError(f"Unsupported SWF signature: {signature!r}")

    expected = file_length - 8
    if len(body) != expected:
        raise ValueError(f"Unexpected decompressed body size: got {len(body)}, expected {expected}")

    return version, file_length, body


def parse_rect_size(body: bytes) -> int:
    nbits = body[0] >> 3
    return (5 + nbits * 4 + 7) // 8


def iter_tags(body: bytes):
    pos = parse_rect_size(body) + 4
    index = 0

    while pos < len(body):
        record = read_u16(body, pos)
        pos += 2
        tag_code = record >> 6
        tag_length = record & 0x3F
        if tag_length == 0x3F:
            tag_length = read_u32(body, pos)
            pos += 4
        payload = body[pos : pos + tag_length]
        pos += tag_length
        index += 1
        yield index, tag_code, payload
        if tag_code == 0:
            break


def save_image(image: Image.Image, output_path: Path):
    image.save(output_path, format="PNG")


def pil_from_bytes(image_data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(image_data))
    image.load()
    return image.convert("RGBA")


def extract_jpeg_like(character_id: int, payload: bytes, output_dir: Path, prefix: str):
    image = pil_from_bytes(payload)
    output_path = output_dir / f"{prefix}_{character_id}.png"
    save_image(image, output_path)
    return output_path


def extract_jpeg_with_alpha(character_id: int, image_data: bytes, alpha_data: bytes, output_dir: Path, prefix: str):
    image = pil_from_bytes(image_data)
    alpha = zlib.decompress(alpha_data)
    if len(alpha) != image.width * image.height:
        raise ValueError(f"Unexpected alpha length for character {character_id}: {len(alpha)}")
    image.putalpha(Image.frombytes("L", image.size, alpha))
    output_path = output_dir / f"{prefix}_{character_id}.png"
    save_image(image, output_path)
    return output_path


def rgba_from_argb(raw: bytes) -> bytes:
    out = bytearray(len(raw))
    for i in range(0, len(raw), 4):
        a, r, g, b = raw[i : i + 4]
        out[i : i + 4] = bytes((r, g, b, a))
    return bytes(out)


def rgba_from_xrgb(raw: bytes) -> bytes:
    out = bytearray(len(raw))
    for i in range(0, len(raw), 4):
        _, r, g, b = raw[i : i + 4]
        out[i : i + 4] = bytes((r, g, b, 255))
    return bytes(out)


def decode_lossless(payload: bytes, has_alpha: bool, output_dir: Path, prefix: str):
    character_id = read_u16(payload, 0)
    bitmap_format = payload[2]
    width = read_u16(payload, 3)
    height = read_u16(payload, 5)

    offset = 7
    color_table_size = None
    if bitmap_format == 3:
        color_table_size = payload[offset] + 1
        offset += 1

    bitmap_data = zlib.decompress(payload[offset:])

    if bitmap_format == 5:
        if has_alpha:
            image = Image.frombytes("RGBA", (width, height), rgba_from_argb(bitmap_data))
        else:
            image = Image.frombytes("RGBA", (width, height), rgba_from_xrgb(bitmap_data))
    elif bitmap_format == 3:
        entry_size = 4 if has_alpha else 3
        palette_bytes = bitmap_data[: color_table_size * entry_size]
        indices = bitmap_data[color_table_size * entry_size :]
        row_stride = (width + 3) & ~3
        rows = bytearray()

        if has_alpha:
            palette = [palette_bytes[i : i + 4] for i in range(0, len(palette_bytes), 4)]
        else:
            palette = [palette_bytes[i : i + 3] + b"\xff" for i in range(0, len(palette_bytes), 3)]

        for y in range(height):
            row = indices[y * row_stride : y * row_stride + width]
            for idx in row:
                rows.extend(palette[idx])
        image = Image.frombytes("RGBA", (width, height), bytes(rows))
    else:
        raise ValueError(f"Unsupported lossless bitmap format {bitmap_format} for character {character_id}")

    output_path = output_dir / f"{prefix}_{character_id}.png"
    save_image(image, output_path)
    return output_path


def extract_images(input_path: Path, output_dir: Path):
    raw_data = input_path.read_bytes()
    body = None
    decode_mode = None
    last_error = None

    for swf_data, mode in ensure_swf_candidates(raw_data):
        try:
            _, _, body = decompress_swf_body(swf_data)
            decode_mode = mode
            break
        except Exception as exc:
            last_error = exc

    if body is None or decode_mode is None:
        raise last_error if last_error else ValueError("Could not decode SWF data.")

    output_dir.mkdir(parents=True, exist_ok=True)
    saved = []

    for index, tag_code, payload in iter_tags(body):
        if tag_code == 21:
            character_id = read_u16(payload, 0)
            path = extract_jpeg_like(character_id, payload[2:], output_dir, f"{index:03d}_jpeg2")
            saved.append(path)
        elif tag_code == 35:
            character_id = read_u16(payload, 0)
            alpha_offset = read_u32(payload, 2)
            image_data = payload[6 : 6 + alpha_offset]
            alpha_data = payload[6 + alpha_offset :]
            path = extract_jpeg_with_alpha(character_id, image_data, alpha_data, output_dir, f"{index:03d}_jpeg3")
            saved.append(path)
        elif tag_code == 90:
            character_id = read_u16(payload, 0)
            alpha_offset = read_u32(payload, 2)
            image_data = payload[8 : 8 + alpha_offset]
            alpha_data = payload[8 + alpha_offset :]
            path = extract_jpeg_with_alpha(character_id, image_data, alpha_data, output_dir, f"{index:03d}_jpeg4")
            saved.append(path)
        elif tag_code == 20:
            path = decode_lossless(payload, has_alpha=False, output_dir=output_dir, prefix=f"{index:03d}_lossless")
            saved.append(path)
        elif tag_code == 36:
            path = decode_lossless(payload, has_alpha=True, output_dir=output_dir, prefix=f"{index:03d}_lossless2")
            saved.append(path)

    return decode_mode, saved


def main():
    parser = argparse.ArgumentParser(description="Extract bitmap images from SWF files.")
    parser.add_argument("input", type=Path, help="SWF path or encoded activity SWF path")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Output directory. Defaults to <input_stem>_images",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or args.input.with_name(f"{args.input.stem}_images")
    decode_mode, saved = extract_images(args.input, output_dir)

    print(f"[info] Decode mode: {decode_mode}")
    print(f"[info] Output directory: {output_dir.resolve()}")
    print(f"[info] Extracted {len(saved)} images")
    for path in saved[:20]:
        print(f"  - {path.name}")
    if len(saved) > 20:
        print(f"  ... and {len(saved) - 20} more")


if __name__ == "__main__":
    main()
