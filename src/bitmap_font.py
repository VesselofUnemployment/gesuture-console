"""Render compact profile labels using Pillow's built-in font."""
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


@lru_cache(maxsize=1)
def atlas():
    font = ImageFont.load_default(size=10)
    chars = ''.join(chr(n) for n in range(32, 127)) + 'ÇçĞğİıÖöŞşÜü'
    tiles = {}
    for char in chars:
        tile = Image.new('L', (12, 13))
        ImageDraw.Draw(tile).text((1, 10), char, fill=255, font=font, anchor='ls')
        tiles[char] = np.asarray(tile)
    return tiles


@lru_cache(maxsize=256)
def text_layout(text, core_height, gap=1, max_width=1000):
    tiles = atlas()
    pieces, stops, width = [], [], 0
    for char in text:
        tile = tiles.get(char, tiles["?"])
        occupied = np.flatnonzero(np.any(tile > 0, axis=0))
        glyph = tile[:, occupied[0]:occupied[-1]+1] if occupied.size else np.zeros((13, 3), np.uint8)
        if pieces:
            pieces.append(np.zeros((13, gap), np.uint8))
            width += gap
        pieces.append(glyph)
        width += glyph.shape[1]
        stops.append(width)
    mask = np.concatenate(pieces, axis=1) if pieces else np.zeros((13, 1), np.uint8)
    rows = np.flatnonzero(np.any(mask > 0, axis=1))
    if rows.size:
        mask = mask[rows[0]:rows[-1]+1]
    factor = min(core_height / 7, max_width / mask.shape[1])
    h, w = max(1, round(mask.shape[0]*factor)), max(1, round(mask.shape[1]*factor))
    mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
    return mask, tuple(round(stop * w / width) for stop in stops)


def text_mask(text, core_height, gap=1, max_width=1000, visible=None):
    mask, stops = text_layout(text, core_height, gap, max_width)
    if visible is None:
        return mask
    result = mask.copy()
    count = max(0, min(len(stops), int(visible)))
    end = 0 if not count else stops[count-1]
    result[:, end:] = 0
    return result


def fields_mask(fields, core_height, max_width):
    # Wrap long field lists to keep the text readable.
    lines, current = [], "Field: "
    for field in fields:
        candidate = current + (" & " if current != "Field: " else "") + field
        if text_mask(candidate, core_height).shape[1] > max_width and current != "Field: ":
            lines.append(current)
            current = "& " + field
        else:
            current = candidate
    lines.append(current)
    masks = [text_mask(line, core_height, max_width=max_width) for line in lines]
    width = max(mask.shape[1] for mask in masks)
    padded = [np.pad(mask, ((0, 0), (0, width-mask.shape[1]))) for mask in masks]
    gap = np.zeros((max(3, round(core_height/2)), width), np.uint8)
    pieces = []
    for index, mask in enumerate(padded):
        if index:
            pieces.append(gap)
        pieces.append(mask)
    return np.concatenate(pieces, axis=0)
