#!/usr/bin/env python3
"""Генерирует brand-иконки интеграции.

С Home Assistant 2026.3 кастомные интеграции возят иконку с собой, в каталоге
``custom_components/<домен>/brand/``. Репозиторий home-assistant/brands заявки
от кастомных интеграций больше не принимает.

Требования: PNG, прозрачность, соотношение 1:1, 256x256 и 512x512 (@2x),
минимум пустого места по краям.

    python tools/make_brand_icon.py

Рисунок **оригинальный**: метка на карте. Логотип 2ГИС сюда сознательно не
берётся — это товарный знак, и хранить его в репозитории ни к чему.
Если захочется официальный знак, файлы просто заменяются вручную.
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "twogis_friends" / "brand"

#: сглаживание: рисуем крупно, потом уменьшаем
SUPERSAMPLE = 4

GREEN_TOP = (46, 182, 76)
GREEN_BOTTOM = (22, 138, 55)
WHITE = (255, 255, 255, 255)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def _gradient(size: int) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    px = grad.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = tuple(
            round(a + (b - a) * t) for a, b in zip(GREEN_TOP, GREEN_BOTTOM)
        )
    return grad.resize((size, size))


def _pin(draw: ImageDraw.ImageDraw, size: int) -> None:
    """Классическая метка: круг сверху, остриё снизу, отверстие в центре."""
    cx = size / 2
    head_r = size * 0.185
    head_cy = size * 0.395
    tip_y = size * 0.775

    draw.ellipse(
        (cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r),
        fill=WHITE,
    )
    # остриё: треугольник от боков окружности к нижней точке, слегка вогнутый
    shoulder = head_r * 0.86
    draw.polygon(
        [
            (cx - shoulder, head_cy + head_r * 0.52),
            (cx + shoulder, head_cy + head_r * 0.52),
            (cx, tip_y),
        ],
        fill=WHITE,
    )
    # отверстие
    hole_r = head_r * 0.40
    draw.ellipse(
        (cx - hole_r, head_cy - hole_r, cx + hole_r, head_cy + hole_r),
        fill=(0, 0, 0, 0),
    )


def build(size: int) -> Image.Image:
    big = size * SUPERSAMPLE

    base = _gradient(big).convert("RGBA")
    base.putalpha(_rounded_mask(big, radius=round(big * 0.22)))

    overlay = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    _pin(ImageDraw.Draw(overlay), big)

    # накладываем метку так, чтобы её «отверстие» оставалось прозрачным
    # не насквозь, а показывало фон: собираем поверх копии фона
    out = Image.alpha_composite(base, overlay)
    return out.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        path = OUT_DIR / name
        build(size).save(path, "PNG", optimize=True)
        print(f"  {path.relative_to(OUT_DIR.parent.parent.parent)}  {size}x{size}  "
              f"{path.stat().st_size} байт")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
