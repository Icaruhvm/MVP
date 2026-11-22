"""Pistola 3D no espaço da câmera. O cano aponta para o centro da mira."""

from __future__ import annotations

import math

import pygame

from valaim.mathutil import HORIZONTAL_FOV_DEG, vertical_fov_deg

Vec3 = tuple[float, float, float]
Quad = tuple[Vec3, Vec3, Vec3, Vec3, tuple[int, int, int]]

# Posição da empunhadura e ponto para onde o cano olha (centro da tela, um pouco à frente).
_PIVOT: Vec3 = (0.105, -0.112, 0.42)
_AIM: Vec3 = (0.0, 0.0, 1.15)


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a: Vec3) -> Vec3:
    length = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / length, a[1] / length, a[2] / length)


def aim_basis(recoil_deg: float = 0.0) -> tuple[Vec3, Vec3, Vec3]:
    """Eixos da arma: direita, cima, cano. O cano passa pelo centro da mira."""
    forward = _norm(_sub(_AIM, _PIVOT))
    right = _norm(_cross((0.0, 1.0, 0.0), forward))
    up = _norm(_cross(forward, right))
    if recoil_deg:
        angle = math.radians(recoil_deg)
        # Sobe a boca em volta da empunhadura.
        forward = _norm(_add(_mul(forward, math.cos(angle)), _mul(up, math.sin(angle))))
        up = _norm(_cross(forward, right))
    return right, up, forward


def _place(local: Vec3, recoil_deg: float) -> Vec3:
    right, up, forward = aim_basis(recoil_deg)
    return _add(_PIVOT, _add(_add(_mul(right, local[0]), _mul(up, local[1])), _mul(forward, local[2])))


def _box(origin: Vec3, size: Vec3, color: tuple[int, int, int]) -> list[Quad]:
    x, y, z = origin
    sx, sy, sz = size
    p = [
        (x, y, z),
        (x + sx, y, z),
        (x + sx, y + sy, z),
        (x, y + sy, z),
        (x, y, z + sz),
        (x + sx, y, z + sz),
        (x + sx, y + sy, z + sz),
        (x, y + sy, z + sz),
    ]
    faces = (
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (3, 2, 6, 7),
        (0, 3, 7, 4),
        (1, 5, 6, 2),
    )
    return [(p[a], p[b], p[c], p[d], color) for a, b, c, d in faces]


def _mesh() -> list[Quad]:
    """Peças em metros, cano no eixo local +Z, origem na traseira do ferrolho."""
    slide = (46, 48, 52)
    frame = (34, 36, 40)
    dark = (22, 24, 26)
    grip = (28, 30, 32)
    parts: list[Quad] = []
    parts += _box((-0.014, -0.012, 0.02), (0.028, 0.034, 0.16), slide)
    parts += _box((-0.008, -0.004, 0.17), (0.016, 0.016, 0.055), dark)
    parts += _box((-0.003, 0.022, 0.20), (0.006, 0.012, 0.008), (70, 72, 76))
    parts += _box((-0.010, 0.022, 0.04), (0.006, 0.010, 0.012), (70, 72, 76))
    parts += _box((0.004, 0.022, 0.04), (0.006, 0.010, 0.012), (70, 72, 76))
    parts += _box((-0.016, -0.028, 0.03), (0.032, 0.020, 0.11), frame)
    parts += _box((-0.013, -0.105, 0.045), (0.026, 0.080, 0.038), grip)
    parts += _box((-0.012, -0.055, 0.09), (0.024, 0.012, 0.045), dark)
    parts += _box((-0.014, -0.118, 0.04), (0.028, 0.016, 0.042), (48, 50, 54))
    return parts


def _project(point: Vec3, width: int, height: int) -> tuple[float, float, float] | None:
    x, y, z = point
    if z <= 0.05:
        return None
    hfov = math.radians(HORIZONTAL_FOV_DEG * 0.5)
    vfov = math.radians(vertical_fov_deg() * 0.5)
    sx = (x / (z * math.tan(hfov)) * 0.5 + 0.5) * width
    sy = (0.5 - y / (z * math.tan(vfov)) * 0.5) * height
    return sx, sy, z


def _shade(normal: Vec3, color: tuple[int, int, int]) -> tuple[int, int, int]:
    light = _norm((-0.25, 0.55, 0.75))
    lit = 0.22 + 0.78 * max(0.0, _dot(normal, light))
    return tuple(max(0, min(255, int(channel * lit))) for channel in color)


class Viewmodel:
    def __init__(self) -> None:
        self.pitch = 0.0
        self.pitch_vel = 0.0
        self.slide = 0.0
        self.flash = 0.0
        self._parts = _mesh()

    def kick(self) -> None:
        self.pitch_vel += 46.0
        self.slide = 1.0
        self.flash = 1.0

    def update(self, dt: float) -> None:
        self.pitch_vel += (-70.0 * self.pitch - 11.0 * self.pitch_vel) * dt
        self.pitch += self.pitch_vel * dt
        self.pitch = max(-1.0, min(8.0, self.pitch))
        self.slide = max(0.0, self.slide - dt * 14.0)
        self.flash = max(0.0, self.flash - dt * 18.0)

    def draw(self, screen: pygame.Surface) -> None:
        width, height = screen.get_size()
        painted: list[tuple[float, list[tuple[float, float]], tuple[int, int, int]]] = []
        slide_back = self.slide * 0.012
        for a, b, c, d, color in self._parts:
            corners = []
            for vertex in (a, b, c, d):
                local = vertex
                if vertex[2] > 0.015:
                    local = (vertex[0], vertex[1], vertex[2] - slide_back)
                corners.append(_place(local, self.pitch))
            projected = [_project(point, width, height) for point in corners]
            if any(item is None for item in projected):
                continue
            normal = _norm(_cross(_sub(corners[1], corners[0]), _sub(corners[2], corners[0])))
            if normal[2] > 0.15:
                continue
            pts = [(item[0], item[1]) for item in projected if item is not None]
            depth = sum(item[2] for item in projected if item is not None) / 4.0
            painted.append((depth, pts, _shade(normal, color)))
        for _depth, polygon, color in sorted(painted, key=lambda item: -item[0]):
            pygame.draw.polygon(screen, color, polygon)
        if self.flash > 0.05:
            muzzle = _project(_place((0.0, 0.004, 0.23 - slide_back), self.pitch), width, height)
            if muzzle is not None:
                radius = int(4 + 10 * self.flash)
                pygame.draw.circle(screen, (255, 220, 150), (int(muzzle[0]), int(muzzle[1])), radius)
