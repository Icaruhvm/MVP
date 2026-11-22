"""Mira no padrão do Valorant: linhas internas/externas, ponto, contorno, HEX."""

from __future__ import annotations

import pygame

COLORS = [
    ("Branco", "#FFFFFF"),
    ("Verde", "#00FF00"),
    ("Verde-amarelado", "#BFFF00"),
    ("Amarelo", "#FFFF00"),
    ("Ciano", "#00FFFF"),
    ("Rosa", "#FF77C8"),
    ("Vermelho", "#FF4655"),
]


def default_crosshair() -> dict:
    return {
        "name": "classica",
        "color": "#00FF00",
        "outline_enabled": True,
        "outline_opacity": 0.5,
        "outline_thickness": 1,
        "center_dot": False,
        "center_dot_opacity": 1.0,
        "center_dot_thickness": 2,
        "inner_enabled": True,
        "inner_opacity": 1.0,
        "inner_length": 6,
        "inner_thickness": 2,
        "inner_offset": 3,
        "outer_enabled": True,
        "outer_opacity": 0.35,
        "outer_length": 2,
        "outer_thickness": 2,
        "outer_offset": 10,
    }


def presets() -> dict[str, dict]:
    classic = default_crosshair()
    dot = default_crosshair()
    dot.update(
        {
            "name": "ponto",
            "center_dot": True,
            "center_dot_thickness": 2,
            "inner_enabled": False,
            "outer_enabled": False,
        }
    )
    precise = default_crosshair()
    precise.update(
        {
            "name": "precisao",
            "inner_length": 4,
            "inner_thickness": 2,
            "inner_offset": 2,
            "outer_enabled": False,
            "center_dot": True,
            "center_dot_thickness": 1,
        }
    )
    return {"classica": classic, "ponto": dot, "precisao": precise}


def parse_hex(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return (0, 255, 0)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return (0, 255, 0)


def _line(surface: pygame.Surface, color: tuple[int, int, int], cx: int, cy: int, ox: int, oy: int, length: int, thick: int, axis: str) -> None:
    if length <= 0 or thick <= 0:
        return
    if axis == "h":
        rect = pygame.Rect(cx + ox, cy - thick // 2, length, thick)
    elif axis == "h-":
        rect = pygame.Rect(cx - ox - length, cy - thick // 2, length, thick)
    elif axis == "v":
        rect = pygame.Rect(cx - thick // 2, cy + oy, thick, length)
    else:
        rect = pygame.Rect(cx - thick // 2, cy - oy - length, thick, length)
    pygame.draw.rect(surface, color, rect)


def _arms(surface: pygame.Surface, color: tuple[int, int, int], cx: int, cy: int, offset: int, length: int, thick: int) -> None:
    _line(surface, color, cx, cy, offset, offset, length, thick, "h")
    _line(surface, color, cx, cy, offset, offset, length, thick, "h-")
    _line(surface, color, cx, cy, offset, offset, length, thick, "v")
    _line(surface, color, cx, cy, offset, offset, length, thick, "v-")


def build_surface(cfg: dict, size: int = 256) -> pygame.Surface:
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = size // 2
    rgb = parse_hex(str(cfg.get("color", "#00FF00")))
    outline = cfg.get("outline_enabled", True)
    o_thick = int(cfg.get("outline_thickness", 1))
    o_alpha = int(max(0.0, min(1.0, float(cfg.get("outline_opacity", 0.5)))) * 255)
    outline_color = (0, 0, 0, o_alpha)

    def draw_group(enabled: bool, opacity: float, length: int, thick: int, offset: int) -> None:
        if not enabled or length <= 0 or opacity <= 0:
            return
        color = (*rgb, int(max(0.0, min(1.0, opacity)) * 255))
        if outline and o_thick > 0 and o_alpha > 0:
            _arms(surf, outline_color, cx, cy, max(0, offset - 0), length, thick + o_thick * 2)
            # Expande o contorno também no comprimento.
            _arms(surf, outline_color, cx, cy, max(0, offset - o_thick), length + o_thick * 2, thick + o_thick * 2)
        _arms(surf, color, cx, cy, offset, length, thick)

    draw_group(
        bool(cfg.get("inner_enabled", True)),
        float(cfg.get("inner_opacity", 1)),
        int(cfg.get("inner_length", 6)),
        int(cfg.get("inner_thickness", 2)),
        int(cfg.get("inner_offset", 3)),
    )
    draw_group(
        bool(cfg.get("outer_enabled", True)),
        float(cfg.get("outer_opacity", 0.35)),
        int(cfg.get("outer_length", 2)),
        int(cfg.get("outer_thickness", 2)),
        int(cfg.get("outer_offset", 10)),
    )
    if cfg.get("center_dot"):
        thick = max(1, int(cfg.get("center_dot_thickness", 2)))
        alpha = int(max(0.0, min(1.0, float(cfg.get("center_dot_opacity", 1)))) * 255)
        if outline and o_thick > 0 and o_alpha > 0:
            pygame.draw.rect(
                surf,
                outline_color,
                pygame.Rect(cx - (thick + o_thick * 2) // 2, cy - (thick + o_thick * 2) // 2, thick + o_thick * 2, thick + o_thick * 2),
            )
        pygame.draw.rect(surf, (*rgb, alpha), pygame.Rect(cx - thick // 2, cy - thick // 2, thick, thick))
    return surf


def blit_center(screen: pygame.Surface, sprite: pygame.Surface) -> None:
    rect = sprite.get_rect(center=screen.get_rect().center)
    screen.blit(sprite, rect)
