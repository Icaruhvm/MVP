"""Viewmodel estilizado de pistola. Geometria original, desenhada com caixas."""

from __future__ import annotations

import math
from typing import Iterable

import pygame

Vec3 = tuple[float, float, float]

# Caixas no espaço da arma: (centro, tamanho, cor, desliza com o ferrolho).
_PARTS: list[tuple[Vec3, Vec3, tuple[int, int, int], bool]] = [
    ((0.00, -0.05, 0.62), (0.055, 0.07, 0.34), (42, 44, 48), True),
    ((0.00, -0.02, 0.78), (0.028, 0.028, 0.16), (28, 28, 30), True),
    ((0.00, -0.105, 0.56), (0.05, 0.055, 0.20), (32, 34, 38), False),
    ((0.00, -0.19, 0.50), (0.046, 0.14, 0.07), (36, 32, 30), False),
    ((0.00, -0.075, 0.46), (0.07, 0.045, 0.09), (48, 50, 54), False),
]


class Viewmodel:
    def __init__(self) -> None:
        self.pitch = 0.0
        self.pitch_vel = 0.0
        self.slide = 0.0
        self.flash = 0.0

    def kick(self) -> None:
        self.pitch_vel += 7.5
        self.slide = 1.0
        self.flash = 1.0

    def update(self, dt: float) -> None:
        self.pitch_vel += (-58.0 * self.pitch - 9.5 * self.pitch_vel) * dt
        self.pitch += self.pitch_vel * dt
        self.pitch = max(-0.2, min(14.0, self.pitch))
        self.slide = max(0.0, self.slide - dt * 9.5)
        self.flash = max(0.0, self.flash - dt * 14.0)

    def draw(self, screen: pygame.Surface) -> None:
        width, height = screen.get_size()
        faces: list[tuple[float, list[tuple[float, float]], tuple[int, int, int]]] = []
        for center, size, color, slides in _PARTS:
            faces.extend(_box_faces(center, size, color, self.pitch, self.slide if slides else 0.0, width, height))
        if self.flash > 0.05:
            faces.append(_flash(self.pitch, self.flash, width, height))
        for _depth, polygon, color in sorted(faces, key=lambda item: -item[0]):
            if len(polygon) >= 3:
                pygame.draw.polygon(screen, color, polygon)


def _recoil(point: Vec3, pitch_deg: float, slide: float) -> Vec3:
    x, y, z = point
    y += slide * 0.004
    z -= slide * 0.045
    pivot_y, pivot_z = -0.12, 0.48
    angle = math.radians(pitch_deg)
    cy, sy = math.cos(angle), math.sin(angle)
    dy, dz = y - pivot_y, z - pivot_z
    y = pivot_y + cy * dy - sy * dz
    z = pivot_z + sy * dy + cy * dz
    return x, y, z


def _project(point: Vec3, width: int, height: int) -> tuple[float, float, float] | None:
    x, y, z = point
    if z <= 0.08:
        return None
    sx = width * 0.72 + (x / z) * width * 0.85
    sy = height * 0.92 - (y / z) * height * 0.85
    return sx, sy, z


def _box_faces(
    center: Vec3,
    size: Vec3,
    color: tuple[int, int, int],
    pitch: float,
    slide: float,
    width: int,
    height: int,
) -> Iterable[tuple[float, list[tuple[float, float]], tuple[int, int, int]]]:
    hx, hy, hz = size[0] * 0.5, size[1] * 0.5, size[2] * 0.5
    corners = []
    for ix in (-hx, hx):
        for iy in (-hy, hy):
            for iz in (-hz, hz):
                corners.append(_recoil((center[0] + ix, center[1] + iy, center[2] + iz), pitch, slide))
    quads = (
        (0, 1, 3, 2, 0.55),
        (4, 6, 7, 5, 1.0),
        (0, 4, 5, 1, 0.75),
        (2, 3, 7, 6, 0.4),
        (0, 2, 6, 4, 0.85),
        (1, 5, 7, 3, 0.65),
    )
    for a, b, c, d, shade in quads:
        projected = [_project(corners[i], width, height) for i in (a, b, c, d)]
        if any(p is None for p in projected):
            continue
        pts = [(p[0], p[1]) for p in projected if p is not None]
        depth = sum(p[2] for p in projected if p is not None) / 4.0
        tint = tuple(max(0, min(255, int(channel * shade))) for channel in color)
        yield depth, pts, tint


def _flash(pitch: float, amount: float, width: int, height: int) -> tuple[float, list[tuple[float, float]], tuple[int, int, int]]:
    origin = _recoil((0.0, -0.02, 0.90), pitch, 0.0)
    projected = _project(origin, width, height)
    if projected is None:
        return 0.0, [], (255, 220, 140)
    sx, sy, depth = projected
    size = 10 + 18 * amount
    polygon = [(sx, sy - size), (sx + size * 0.7, sy), (sx, sy + size * 0.45), (sx - size * 0.7, sy)]
    return depth - 0.01, polygon, (255, int(210 * amount + 40), 120)
