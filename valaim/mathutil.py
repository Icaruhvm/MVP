"""Escala angular do Valorant e projeção com FOV horizontal fixo."""

from __future__ import annotations

import math
from typing import Optional, Tuple

# Graus de yaw/pitch por count do mouse, por 1.0 de sensibilidade.
VALORANT_YAW = 0.07
HORIZONTAL_FOV_DEG = 103.0
ASPECT_16_9 = 16.0 / 9.0

Vec3 = Tuple[float, float, float]
Vec2 = Tuple[float, float]


def edpi(dpi: float, sensitivity: float) -> float:
    return dpi * sensitivity


def cm_per_360(dpi: float, sensitivity: float) -> float:
    """cm de mouse para girar 360° no Valorant.

    counts/360 = 360 / (sens * 0.07)
    cm/360 = counts/360 / DPI * 2.54
    """
    counts = 360.0 / (dpi * sensitivity * VALORANT_YAW)
    return counts * 2.54


def degrees_from_counts(counts: float, sensitivity: float) -> float:
    return counts * sensitivity * VALORANT_YAW


def vertical_fov_deg(horizontal_fov: float = HORIZONTAL_FOV_DEG, aspect: float = ASPECT_16_9) -> float:
    half_h = math.radians(horizontal_fov * 0.5)
    return math.degrees(2.0 * math.atan(math.tan(half_h) / aspect))


def clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def length2(v: Vec2) -> float:
    return math.hypot(v[0], v[1])


def dot2(a: Vec2, b: Vec2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def angle_between_forward_and_point(forward: Vec3, to_point: Vec3) -> float:
    """Ângulo em graus entre o olhar e o vetor até o alvo."""
    fl = math.sqrt(forward[0] ** 2 + forward[1] ** 2 + forward[2] ** 2)
    tl = math.sqrt(to_point[0] ** 2 + to_point[1] ** 2 + to_point[2] ** 2)
    if fl < 1e-8 or tl < 1e-8:
        return 0.0
    c = (forward[0] * to_point[0] + forward[1] * to_point[1] + forward[2] * to_point[2]) / (fl * tl)
    c = clamp(c, -1.0, 1.0)
    return math.degrees(math.acos(c))


def camera_basis(yaw_deg: float, pitch_deg: float) -> Tuple[Vec3, Vec3, Vec3]:
    """Retorna (forward, right, up). Yaw 0 olha para +Z. Pitch positivo olha para cima."""
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    forward = (cp * sy, sp, cp * cy)
    right = (cy, 0.0, -sy)
    # up = right × forward (não normalizar de novo: base já é ortonormal)
    up = (
        right[1] * forward[2] - right[2] * forward[1],
        right[2] * forward[0] - right[0] * forward[2],
        right[0] * forward[1] - right[1] * forward[0],
    )
    return forward, right, up


def view_error_degrees(yaw_deg: float, pitch_deg: float, cam: Vec3, target: Vec3) -> Vec2:
    """Erro (horizontal, vertical) em graus. Positivo: alvo à direita / acima da mira."""
    forward, right, up = camera_basis(yaw_deg, pitch_deg)
    dx = target[0] - cam[0]
    dy = target[1] - cam[1]
    dz = target[2] - cam[2]
    z_cam = dx * forward[0] + dy * forward[1] + dz * forward[2]
    x_cam = dx * right[0] + dy * right[1] + dz * right[2]
    y_cam = dx * up[0] + dy * up[1] + dz * up[2]
    if z_cam <= 1e-4:
        return (0.0, 0.0)
    horiz = math.degrees(math.atan2(x_cam, z_cam))
    vert = math.degrees(math.atan2(y_cam, z_cam))
    return (horiz, vert)


def project_point(
    yaw_deg: float,
    pitch_deg: float,
    cam: Vec3,
    point: Vec3,
    width: int,
    height: int,
    hfov: float = HORIZONTAL_FOV_DEG,
) -> Optional[Tuple[float, float, float]]:
    """Projeta um ponto do mundo. None se estiver atrás da câmera."""
    forward, right, up = camera_basis(yaw_deg, pitch_deg)
    dx = point[0] - cam[0]
    dy = point[1] - cam[1]
    dz = point[2] - cam[2]
    z_cam = dx * forward[0] + dy * forward[1] + dz * forward[2]
    if z_cam <= 0.05:
        return None
    x_cam = dx * right[0] + dy * right[1] + dz * right[2]
    y_cam = dx * up[0] + dy * up[1] + dz * up[2]
    vfov = vertical_fov_deg(hfov)
    th = math.tan(math.radians(hfov * 0.5))
    tv = math.tan(math.radians(vfov * 0.5))
    ndc_x = x_cam / (z_cam * th)
    ndc_y = y_cam / (z_cam * tv)
    sx = (ndc_x * 0.5 + 0.5) * width
    sy = (0.5 - ndc_y * 0.5) * height
    return sx, sy, z_cam


def angular_radius_deg(radius_m: float, distance_m: float) -> float:
    if distance_m <= 1e-6:
        return 180.0
    return math.degrees(math.atan(radius_m / distance_m))


def residual_mm(error_deg: float, radius_deg: float, distance_m: float) -> float:
    """Milímetros além da borda do alvo. 0 se o clique acertou."""
    extra = max(0.0, error_deg - radius_deg)
    return math.tan(math.radians(extra)) * distance_m * 1000.0


class FlickTracker:
    """Mede overshoot, undershoot, reação e eficiência de um alvo estático."""

    def __init__(self, initial_error: Vec2) -> None:
        self.e0 = initial_error
        self.e0_len = length2(initial_error)
        self.last_e = initial_error
        self.path = 0.0
        self.max_overshoot = 0.0
        self.moved = False
        self.reaction_ms: Optional[float] = None
        self.first_stop_undershoot: Optional[float] = None
        self.reversals_near = 0
        self._prev_step: Optional[Vec2] = None
        self._seen_motion = False

    def update(self, error: Vec2, dt: float, elapsed_ms: float) -> None:
        step = (error[0] - self.last_e[0], error[1] - self.last_e[1])
        step_len = length2(step)
        self.path += step_len
        if not self.moved and self.path >= 0.12:
            self.moved = True
            self.reaction_ms = elapsed_ms
        if self.e0_len > 1e-4:
            ux = self.e0[0] / self.e0_len
            uy = self.e0[1] / self.e0_len
            cancelled = (self.e0[0] - error[0]) * ux + (self.e0[1] - error[1]) * uy
            over = cancelled - self.e0_len
            if over > self.max_overshoot:
                self.max_overshoot = over
        if length2(error) < 1.15 and self._prev_step is not None and step_len > 0.01:
            if dot2(step, self._prev_step) < 0.0:
                self.reversals_near += 1
        speed = step_len / dt if dt > 1e-5 else 0.0
        if self.moved:
            self._seen_motion = True
        progressed = 0.0
        if self.e0_len > 1e-4:
            progressed = (self.e0_len - length2(error)) / self.e0_len
        if (
            self._seen_motion
            and self.first_stop_undershoot is None
            and self.reaction_ms is not None
            and elapsed_ms > self.reaction_ms + 35.0
            and speed < 18.0
            and progressed >= 0.45
        ):
            self.first_stop_undershoot = length2(error)
        if step_len > 0.004:
            self._prev_step = step
        self.last_e = error

    def snapshot(self, elapsed_ms: float, hit: bool, distance_m: float, radius_m: float) -> dict:
        err = length2(self.last_e)
        radius_deg = angular_radius_deg(radius_m, distance_m)
        efficiency = 1.0
        if self.path > 1e-4:
            efficiency = clamp(self.e0_len / self.path, 0.0, 1.0)
        # Acerto zera o undershoot. Um clique fora mede o que faltou naquele instante,
        # não a primeira desaceleração (micro-flick é lento e isso inflava o número).
        if hit:
            undershoot = 0.0
        else:
            undershoot = err
        return {
            "initial_angle_deg": round(self.e0_len, 4),
            "reaction_ms": None if self.reaction_ms is None else round(self.reaction_ms, 2),
            "time_to_click_ms": round(elapsed_ms, 2),
            "overshoot_deg": round(max(0.0, self.max_overshoot), 4),
            "undershoot_deg": round(max(0.0, undershoot), 4),
            "error_at_click_deg": round(err, 4),
            "path_length_deg": round(self.path, 4),
            "path_efficiency": round(efficiency, 4),
            "near_target_reversals": self.reversals_near,
            "residual_x_deg": round(self.last_e[0], 4),
            "residual_y_deg": round(self.last_e[1], 4),
            "hit": hit,
            "miss_beyond_edge_mm": round(0.0 if hit else residual_mm(err, radius_deg, distance_m), 3),
            "target_distance_m": round(distance_m, 3),
            "target_radius_m": round(radius_m, 3),
        }
