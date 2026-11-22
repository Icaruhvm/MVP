"""Viewmodel da pistola a partir das fotos, com recuo e ferrolho."""

from __future__ import annotations

import sys
from pathlib import Path

import pygame


def _asset_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "valaim" / "assets"
    return Path(__file__).resolve().parent / "assets"


def _load(name: str) -> pygame.Surface | None:
    path = _asset_dir() / name
    if not path.exists():
        return None
    image = pygame.image.load(str(path)).convert()
    image.set_colorkey(image.get_at((0, 0)))
    return image


class Viewmodel:
    def __init__(self) -> None:
        self.pitch = 0.0
        self.pitch_vel = 0.0
        self.slide = 0.0
        self.flash = 0.0
        self.rest: pygame.Surface | None = None
        self.back: pygame.Surface | None = None
        self._ready = False
        self._scaled: dict[tuple[int, int], pygame.Surface] = {}

    def _ensure_loaded(self) -> None:
        if self._ready or pygame.display.get_surface() is None:
            return
        self.rest = _load("pistol.jpg")
        self.back = _load("pistol_slide.jpg")
        self._ready = True

    def kick(self) -> None:
        self.pitch_vel += 9.0
        self.slide = 1.0
        self.flash = 1.0

    def update(self, dt: float) -> None:
        self.pitch_vel += (-52.0 * self.pitch - 8.0 * self.pitch_vel) * dt
        self.pitch += self.pitch_vel * dt
        self.pitch = max(-0.4, min(12.0, self.pitch))
        self.slide = max(0.0, self.slide - dt * 11.0)
        self.flash = max(0.0, self.flash - dt * 16.0)

    def _frame(self, width: int) -> pygame.Surface | None:
        source = self.back if self.slide > 0.45 and self.back is not None else self.rest
        if source is None:
            return None
        target_w = max(480, int(width * 0.46))
        key = (id(source), target_w)
        cached = self._scaled.get(key)
        if cached is None:
            height = int(source.get_height() * (target_w / source.get_width()))
            cached = pygame.transform.smoothscale(source, (target_w, height))
            cached.set_colorkey(source.get_colorkey())
            self._scaled[key] = cached
        return cached

    def draw(self, screen: pygame.Surface) -> None:
        self._ensure_loaded()
        width, height = screen.get_size()
        frame = self._frame(width)
        if frame is None:
            return
        spun = pygame.transform.rotozoom(frame, self.pitch * 0.55, 1.0)
        spun.set_colorkey(frame.get_colorkey())
        kick_y = int(self.pitch * 3.2)
        rect = spun.get_rect()
        rect.right = width + int(width * 0.02)
        rect.bottom = height + int(height * 0.08) - kick_y
        screen.blit(spun, rect)
        if self.flash > 0.08:
            muzzle = (rect.left + int(rect.width * 0.18), rect.top + int(rect.height * 0.28) - kick_y)
            radius = int(10 + 16 * self.flash)
            pygame.draw.circle(screen, (255, 214, 140), muzzle, radius)
            pygame.draw.circle(screen, (255, 244, 210), muzzle, max(3, radius // 2))
