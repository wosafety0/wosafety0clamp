"""Minimal PNG decode/encode used by screen-geometry calibration.

Standard library only. The game surface is drawn on a canvas, so row geometry
has to be measured from pixels; keeping the codec dependency-free avoids
requiring numpy/OpenCV just to read a screenshot.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS_BY_COLOR_TYPE = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_SUPPORTED_BIT_DEPTH = 8


class PngError(RuntimeError):
    """The PNG cannot be decoded or encoded safely."""


@dataclass(frozen=True, slots=True)
class RgbImage:
    """An 8-bit image with 1, 2, 3 or 4 interleaved channels."""

    width: int
    height: int
    channels: int
    pixels: bytes

    def __post_init__(self) -> None:
        for name in ("width", "height", "channels"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.channels not in (1, 2, 3, 4):
            raise ValueError("channels must be 1, 2, 3 or 4")
        expected = self.width * self.height * self.channels
        if len(self.pixels) != expected:
            raise ValueError(
                f"pixel buffer must hold {expected} bytes, got {len(self.pixels)}"
            )

    def luminance(self, x: int, y: int) -> int:
        """Return the perceptual-free average of the first three channels."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError("pixel coordinate is outside the image")
        base = (y * self.width + x) * self.channels
        pixels = self.pixels
        if self.channels == 1:
            return pixels[base]
        if self.channels == 2:
            return pixels[base]
        return (pixels[base] + pixels[base + 1] + pixels[base + 2]) // 3

    def crop(self, left: int, top: int, right: int, bottom: int) -> "RgbImage":
        """Return an RGB crop; coordinates are half-open and validated."""
        if not (0 <= left < right <= self.width):
            raise ValueError("crop columns are outside the image")
        if not (0 <= top < bottom <= self.height):
            raise ValueError("crop rows are outside the image")
        width = right - left
        rows = bytearray(width * (bottom - top) * 3)
        source = self.pixels
        for offset, y in enumerate(range(top, bottom)):
            start = (y * self.width + left) * self.channels
            end = start + width * self.channels
            strip = source[start:end]
            if self.channels == 3:
                rows[offset * width * 3:(offset + 1) * width * 3] = strip
            else:
                target = offset * width * 3
                for index in range(width):
                    base = index * self.channels
                    out = target + index * 3
                    if self.channels >= 3:
                        rows[out] = strip[base]
                        rows[out + 1] = strip[base + 1]
                        rows[out + 2] = strip[base + 2]
                    else:
                        value = strip[base]
                        rows[out] = rows[out + 1] = rows[out + 2] = value
        return RgbImage(width, bottom - top, 3, bytes(rows))


def decode_png(path: Path | str) -> RgbImage:
    """Decode an 8-bit, non-interlaced PNG into an :class:`RgbImage`."""
    data = Path(path).read_bytes()
    if not data.startswith(_PNG_SIGNATURE):
        raise PngError("file is not a PNG")
    position = len(_PNG_SIGNATURE)
    compressed = bytearray()
    width = height = color_type = None
    while position + 8 <= len(data):
        (length,) = struct.unpack(">I", data[position:position + 4])
        chunk_type = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        if len(body) != length:
            raise PngError("truncated PNG chunk")
        if chunk_type == b"IHDR":
            if length < 10:
                raise PngError("PNG header is too short")
            width, height, depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", body[:13]
            )
            if depth != _SUPPORTED_BIT_DEPTH:
                raise PngError(f"unsupported PNG bit depth: {depth}")
            if color_type not in _CHANNELS_BY_COLOR_TYPE:
                raise PngError(f"unsupported PNG color type: {color_type}")
            if interlace != 0:
                raise PngError("interlaced PNG is unsupported")
        elif chunk_type == b"IDAT":
            compressed.extend(body)
        elif chunk_type == b"IEND":
            break
        position += 12 + length
    if width is None or height is None or color_type is None:
        raise PngError("PNG header is missing")
    if width < 1 or height < 1:
        raise PngError("PNG has no pixels")
    if not compressed:
        raise PngError("PNG has no image data")

    channels = _CHANNELS_BY_COLOR_TYPE[color_type]
    stride = width * channels
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error as exc:  # pragma: no cover - corrupt input only
        raise PngError(f"PNG image data is corrupt: {exc}") from exc
    if len(raw) < height * (stride + 1):
        raise PngError("PNG image data is truncated")

    pixels = bytearray(height * stride)
    previous = bytearray(stride)
    offset = 0
    for y in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        if filter_type == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                up = previous[index]
                corner = previous[index - channels] if index >= channels else 0
                estimate = left + up - corner
                left_gap = abs(estimate - left)
                up_gap = abs(estimate - up)
                corner_gap = abs(estimate - corner)
                if left_gap <= up_gap and left_gap <= corner_gap:
                    predictor = left
                elif up_gap <= corner_gap:
                    predictor = up
                else:
                    predictor = corner
                line[index] = (line[index] + predictor) & 0xFF
        elif filter_type != 0:
            raise PngError(f"unsupported PNG row filter: {filter_type}")
        pixels[y * stride:(y + 1) * stride] = line
        previous = line
    return RgbImage(width, height, channels, bytes(pixels))


def encode_png(path: Path | str, image: RgbImage) -> None:
    """Write an RGB image as a filter-0 PNG."""
    if image.channels != 3:
        raise ValueError("only three-channel images can be encoded")
    stride = image.width * 3
    raw = bytearray()
    for y in range(image.height):
        raw.append(0)
        raw.extend(image.pixels[y * stride:(y + 1) * stride])

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 2, 0, 0, 0)
    payload = (
        _PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
    Path(path).write_bytes(payload)


def stack_vertically(images: list[RgbImage], *, gap: int = 4) -> RgbImage:
    """Stack images top-to-bottom with a uniform separator gap."""
    if not images:
        raise ValueError("at least one image is required")
    if isinstance(gap, bool) or not isinstance(gap, int) or gap < 0:
        raise ValueError("gap must be a non-negative integer")
    width = max(image.width for image in images)
    height = sum(image.height for image in images) + gap * (len(images) - 1)
    canvas = bytearray(width * height * 3)
    y_offset = 0
    for image in images:
        if image.channels != 3:
            raise ValueError("only three-channel images can be stacked")
        for y in range(image.height):
            source = y * image.width * 3
            target = (y_offset + y) * width * 3
            canvas[target:target + image.width * 3] = image.pixels[
                source:source + image.width * 3
            ]
        y_offset += image.height + gap
    return RgbImage(width, height, 3, bytes(canvas))
