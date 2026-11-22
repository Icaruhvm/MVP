"""Loop do aim trainer: setup, mira, micro-flicks, strafe e relatório."""

from __future__ import annotations

import array
import ctypes
import json
import math
import os
import random
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pygame

from valaim.analyzer import analyze_sessions, load_sessions
from valaim.crosshair import COLORS, blit_center, build_surface, default_crosshair, parse_hex, presets
from valaim.mathutil import (
    HORIZONTAL_FOV_DEG,
    FlickTracker,
    angular_radius_deg,
    camera_basis,
    cm_per_360,
    degrees_from_counts,
    edpi,
    length2,
    project_point,
    view_error_degrees,
)
from valaim.telemetry import close_session, new_session, sessions_dir, write_session
from valaim.weapon import Viewmodel

def _app_root() -> Path:
    # No .exe, perfil e sessões ficam no AppData. No código-fonte, ficam na pasta do projeto.
    if getattr(sys, "frozen", False):
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ValorantAimLab"
        root.mkdir(parents=True, exist_ok=True)
        return root
    return Path(__file__).resolve().parents[1]


ROOT = _app_root()
WIDTH, HEIGHT = 1600, 900
CAM_Y = 1.6
BG = (14, 16, 20)
PANEL = (22, 26, 32)
ACCENT = (255, 70, 85)
TEXT = (236, 232, 225)
MUTED = (160, 164, 170)
GOOD = (120, 220, 140)
APP = "MVP"


def _dpi_aware() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _enable_raw_relative_mouse() -> None:
    # SDL2 no Windows usa WM_INPUT no modo relativo. Isso ignora "Enhance pointer precision".
    os.environ.setdefault("SDL_MOUSE_RELATIVE_MODE_WARP", "0")


class Camera:
    def __init__(self) -> None:
        self.yaw = 0.0
        self.pitch = 0.0
        self.pos = (0.0, CAM_Y, 0.0)
        self.yaw_rate = 0.0

    def look(self, dx: float, dy: float, sensitivity: float, dt: float) -> None:
        dh = degrees_from_counts(dx, sensitivity)
        dv = degrees_from_counts(dy, sensitivity)
        self.yaw += dh
        # Testado no PC: somar o dy do SDL sobe a mira quando o mouse sobe.
        self.pitch += dv
        self.pitch = max(-89.0, min(89.0, self.pitch))
        self.yaw_rate = dh / dt if dt > 1e-5 else 0.0

    def reset(self) -> None:
        self.yaw = 0.0
        self.pitch = 0.0
        self.yaw_rate = 0.0


class MicroFlicks:
    name = "micro_flicks"

    def __init__(self, cam: Camera) -> None:
        self.cam = cam
        self.pos = (0.0, CAM_Y, 15.0)
        self.radius = 0.12
        self.tracker: FlickTracker | None = None
        self.born_ms = 0.0
        self.elapsed_ms = 0.0
        self.shots: list[dict] = []
        self.just_hit = False
        self.side = 1
        self._spawn(0.0, first=True)

    def _spawn(self, elapsed_ms: float, first: bool = False) -> None:
        # Nascem em torno do centro da pista, não de onde a mira parou.
        # Sem isso, cada acerto empurrava o próximo alvo mais para a direita.
        dist = random.uniform(14.0, 18.0)
        self.side *= -1
        spread = random.uniform(0.7, 2.4)
        h = 0.0 if first and random.random() < 0.35 else self.side * spread
        v = random.uniform(-1.1, 1.1)
        forward, _, _ = camera_basis(h, v)
        self.pos = (forward[0] * dist, CAM_Y + forward[1] * dist, forward[2] * dist)
        self.radius = 0.12
        err = view_error_degrees(self.cam.yaw, self.cam.pitch, self.cam.pos, self.pos)
        self.tracker = FlickTracker(err)
        self.born_ms = elapsed_ms

    def update(self, elapsed_ms: float, dt: float, clicked: bool) -> None:
        assert self.tracker is not None
        self.just_hit = False
        err = view_error_degrees(self.cam.yaw, self.cam.pitch, self.cam.pos, self.pos)
        self.tracker.update(err, dt, elapsed_ms - self.born_ms)
        if not clicked:
            return
        dist = math.dist(self.cam.pos, self.pos)
        hit = length2(err) <= angular_radius_deg(self.radius, dist)
        shot = self.tracker.snapshot(elapsed_ms - self.born_ms, hit, dist, self.radius)
        shot["timeout"] = False
        self.shots.append(shot)
        self.just_hit = hit
        if hit:
            self._spawn(elapsed_ms)

    @property
    def target(self) -> tuple[tuple[float, float, float], float]:
        return self.pos, self.radius


class StrafeTracking:
    name = "strafe_tracking"

    def __init__(self, cam: Camera) -> None:
        self.cam = cam
        self.distance = random.uniform(13.0, 16.0)
        self.x = 0.0
        self.radius = 0.22
        self.direction = random.choice((-1.0, 1.0))
        self.speed = random.uniform(1.15, 1.85)
        self.until = random.uniform(0.95, 1.75)
        self.on_time = 0.0
        self.total = 0.0
        self.lags: list[float] = []
        self.pending_ms: float | None = None
        self.need_sign = self.direction
        self.shots: list[dict] = []
        self._crossed = False
        self._overshoot = 0.0
        self._last_hx = 0.0
        self._born = 0.0
        self._on_target = False
        self.just_hit = False

    @property
    def pos(self) -> tuple[float, float, float]:
        # Plano fixo à frente da origem, na altura da cabeça.
        return (self.x, CAM_Y, self.distance)

    def _flip(self) -> None:
        if self.pending_ms is not None:
            self.lags.append(self.pending_ms)
            self.pending_ms = None
        self.direction *= -1.0
        self.need_sign = self.direction
        self.speed = random.uniform(1.15, 1.85)
        self.pending_ms = 0.0
        self.until = random.uniform(0.95, 1.75)

    def update(self, elapsed_ms: float, dt: float, clicked: bool) -> None:
        self.total += dt
        self.x += self.direction * self.speed * dt
        if abs(self.x) > 2.4:
            self.x = math.copysign(2.4, self.x)
            self._flip()
        self.until -= dt
        if self.until <= 0:
            self._flip()
        self.just_hit = False
        err = view_error_degrees(self.cam.yaw, self.cam.pitch, self.cam.pos, self.pos)
        dist = math.dist(self.cam.pos, self.pos)
        radius_deg = angular_radius_deg(self.radius, dist)
        on_target = length2(err) <= radius_deg
        if on_target and not self._on_target:
            self.just_hit = True
        self._on_target = on_target
        if on_target:
            self.on_time += dt
            self._crossed = False
            self._overshoot = 0.0
        hx = err[0]
        if self._last_hx * hx < 0.0 and abs(hx) > 0.05:
            self._crossed = True
            self._overshoot = abs(hx)
        elif self._crossed:
            self._overshoot = max(self._overshoot, abs(hx))
        self._last_hx = hx
        if self.pending_ms is not None:
            self.pending_ms += dt * 1000.0
            if on_target or self.cam.yaw_rate * self.need_sign > 8.0:
                self.lags.append(self.pending_ms)
                self.pending_ms = None
        if clicked:
            hit = length2(err) <= radius_deg
            self.shots.append(
                {
                    "initial_angle_deg": round(length2(err), 4),
                    "reaction_ms": None,
                    "time_to_click_ms": round(elapsed_ms - self._born, 2),
                    "overshoot_deg": round(self._overshoot if self._crossed else 0.0, 4),
                    "undershoot_deg": round(0.0 if hit or self._crossed else length2(err), 4),
                    "error_at_click_deg": round(length2(err), 4),
                    "path_efficiency": None,
                    "residual_x_deg": round(err[0], 4),
                    "residual_y_deg": round(err[1], 4),
                    "hit": hit,
                    "miss_beyond_edge_mm": 0.0 if hit else round(math.tan(math.radians(max(0.0, length2(err) - radius_deg))) * dist * 1000.0, 3),
                    "target_distance_m": round(dist, 3),
                    "target_radius_m": self.radius,
                    "timeout": False,
                }
            )
            self._born = elapsed_ms

    def summary(self) -> dict:
        lag = sum(self.lags) / len(self.lags) if self.lags else None
        tot = 100.0 * self.on_time / self.total if self.total > 0 else 0.0
        return {
            "time_on_target_pct": round(tot, 2),
            "direction_reversal_lag_ms_mean": None if lag is None else round(lag, 2),
            "reversals": len(self.lags),
        }

    @property
    def target(self) -> tuple[tuple[float, float, float], float]:
        return self.pos, self.radius


def _hit_sound() -> pygame.mixer.Sound | None:
    """Clique grave e curto. Sem harmônico agudo."""
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=256)
        rate = pygame.mixer.get_init()[0]
        channels = pygame.mixer.get_init()[2]
        count = int(rate * 0.04)
        samples = array.array("h")
        for i in range(count):
            t = i / rate
            env = math.exp(-t * 90.0)
            wave = math.sin(2.0 * math.pi * 160.0 * t) * env
            sample = int(max(-1.0, min(1.0, wave)) * 7000)
            for _ in range(channels):
                samples.append(sample)
        sound = pygame.mixer.Sound(buffer=samples.tobytes())
        sound.set_volume(0.2)
        return sound
    except pygame.error:
        return None


def _wrap(font: pygame.font.Font, text: str, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        cur = words[0]
        for word in words[1:]:
            trial = cur + " " + word
            if font.size(trial)[0] <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


class App:
    def __init__(self) -> None:
        _dpi_aware()
        _enable_raw_relative_mouse()
        pygame.init()
        pygame.display.set_caption("MVP")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.weapon = Viewmodel()
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Segoe UI", 22)
        self.small = pygame.font.SysFont("Segoe UI", 16)
        self.title = pygame.font.SysFont("Segoe UI", 32, bold=True)
        self.hit_sound = _hit_sound()
        self.cam = Camera()
        self.state = "setup"
        self.profile = self._load_profile()
        self.crosshair = dict(self.profile.get("crosshair") or default_crosshair())
        self.sprite = build_surface(self.crosshair)
        self.field = 0
        self.editor_i = 0
        self.message = ""
        self.report = ""
        self.exercise = None
        self.session = None
        self.battery_id: str | None = None
        self.queue: list[str] = []
        self.play_t = 0.0
        self.fps_samples: list[float] = []
        self.grabbed = False
        self.hex_buf = ""
        self.setup_fields = ["dpi", "sensitivity", "ads_multiplier"]
        self.draft = self._format_field("dpi")
        self.replace_next = True

    def _profile_path(self) -> Path:
        path = ROOT / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path / "profile.json"

    def _load_profile(self) -> dict:
        path = self._profile_path()
        base = {"dpi": 800, "sensitivity": 0.35, "ads_multiplier": 1.0, "duration_s": 45, "crosshair": default_crosshair()}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                base.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass
        return base

    def _save_profile(self) -> None:
        self.profile["crosshair"] = self.crosshair
        self._profile_path().write_text(json.dumps(self.profile, ensure_ascii=False, indent=2), encoding="utf-8")

    def _sens(self) -> float:
        sens = float(self.profile["sensitivity"])
        if pygame.mouse.get_pressed(3)[2]:
            sens *= float(self.profile["ads_multiplier"])
        return sens

    def _set_grab(self, on: bool) -> None:
        if on == self.grabbed:
            return
        self.grabbed = on
        pygame.event.set_grab(on)
        pygame.mouse.set_visible(not on)
        if on:
            pygame.mouse.get_rel()

    def _rebuild_crosshair(self) -> None:
        self.sprite = build_surface(self.crosshair)
        self._save_profile()

    def run(self) -> None:
        while True:
            dt = self.clock.tick(240) / 1000.0
            dt = min(dt, 0.05)
            if not self._frame(dt):
                break
        pygame.quit()

    def _frame(self, dt: float) -> bool:
        clicked = False
        typed = ""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if self.state == "play":
                        self._finish_exercise()
                    elif self.state == "setup":
                        return False
                    else:
                        self._set_grab(False)
                        self.state = "menu" if self.state != "menu" else "setup"
                elif event.key == pygame.K_RETURN and self.state in ("setup", "menu", "report"):
                    self._activate()
                elif event.key in (pygame.K_UP, pygame.K_DOWN) and self.state in ("setup", "menu", "editor"):
                    self._move_cursor(-1 if event.key == pygame.K_UP else 1)
                elif event.key in (pygame.K_LEFT, pygame.K_RIGHT) and self.state in ("setup", "editor"):
                    self._nudge(-1 if event.key == pygame.K_LEFT else 1)
                elif event.key == pygame.K_BACKSPACE:
                    self._backspace()
                elif event.key == pygame.K_F5 and self.state == "menu":
                    self._open_report()
                else:
                    typed = event.unicode
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                clicked = True
                if self.state == "setup":
                    self._setup_click(event.pos)
                elif self.state == "menu":
                    self._menu_click(event.pos)
                elif self.state == "editor":
                    self._editor_click(event.pos)

        if self.state == "play":
            self._play(dt, clicked)
        else:
            self._type_setup(typed)
            self._type_hex(typed)
        self._draw()
        pygame.display.flip()
        return True

    def _move_cursor(self, delta: int) -> None:
        if self.state == "setup":
            self.field = (self.field + delta) % len(self.setup_fields)
        elif self.state == "editor":
            self.editor_i = (self.editor_i + delta) % len(self._editor_rows())
        elif self.state == "menu":
            self.field = (self.field + delta) % 6

    def _format_field(self, key: str) -> str:
        if key == "dpi":
            return str(int(self.profile["dpi"]))
        if key == "sensitivity":
            return f"{float(self.profile['sensitivity']):.3f}"
        return f"{float(self.profile['ads_multiplier']):.2f}"

    def _focus_setup(self, index: int) -> None:
        self._commit_draft()
        self.field = index
        self.draft = self._format_field(self.setup_fields[index])
        self.replace_next = True

    def _commit_draft(self) -> bool:
        if self.state != "setup":
            return True
        key = self.setup_fields[self.field]
        text = self.draft.strip()
        if text in ("", ".", "-"):
            self.draft = self._format_field(key)
            return False
        try:
            value = float(text)
        except ValueError:
            self.message = "Digite só números."
            self.draft = self._format_field(key)
            return False
        limits = {"dpi": (50, 26000), "sensitivity": (0.01, 5.0), "ads_multiplier": (0.1, 2.0)}
        lo, hi = limits[key]
        value = max(lo, min(hi, value))
        self.profile[key] = int(round(value)) if key == "dpi" else round(value, 3 if key == "sensitivity" else 2)
        self.draft = self._format_field(key)
        self.message = ""
        self._save_profile()
        return True

    def _set_profile_value(self, key: str, value: float) -> None:
        limits = {"dpi": (50, 26000), "sensitivity": (0.01, 5.0), "ads_multiplier": (0.1, 2.0), "duration_s": (15, 120)}
        lo, hi = limits[key]
        value = max(lo, min(hi, value))
        if key in ("dpi", "duration_s"):
            self.profile[key] = int(round(value))
        elif key == "sensitivity":
            self.profile[key] = round(value, 3)
        else:
            self.profile[key] = round(value, 2)
        if key in self.setup_fields and self.setup_fields[self.field] == key:
            self.draft = self._format_field(key)
            self.replace_next = True
        self._save_profile()

    def _setup_rects(self) -> dict:
        x0 = 390
        chips = [(pygame.Rect(x0 + i * 150, 196, 136, 46), dpi) for i, dpi in enumerate((400, 800, 1600, 3200))]
        fields = {
            "dpi": pygame.Rect(x0, 258, 420, 58),
            "sensitivity": pygame.Rect(x0, 408, 420, 58),
            "ads_multiplier": pygame.Rect(x0, 560, 220, 52),
        }
        minus = {
            "dpi": pygame.Rect(x0 + 436, 258, 58, 58),
            "sensitivity": pygame.Rect(x0 + 436, 408, 58, 58),
            "ads_multiplier": pygame.Rect(x0 + 236, 560, 52, 52),
        }
        plus = {
            "dpi": pygame.Rect(x0 + 506, 258, 58, 58),
            "sensitivity": pygame.Rect(x0 + 506, 408, 58, 58),
            "ads_multiplier": pygame.Rect(x0 + 296, 560, 52, 52),
        }
        durs = [(pygame.Rect(980 + i * 120, 560, 108, 52), sec) for i, sec in enumerate((30, 45, 60))]
        go = pygame.Rect(x0, 700, 574, 60)
        return {"chips": chips, "fields": fields, "minus": minus, "plus": plus, "durs": durs, "go": go}

    def _setup_click(self, pos: tuple[int, int]) -> None:
        layout = self._setup_rects()
        for rect, dpi in layout["chips"]:
            if rect.collidepoint(pos):
                self._set_profile_value("dpi", dpi)
                self._focus_setup(0)
                return
        for index, key in enumerate(self.setup_fields):
            if layout["fields"][key].collidepoint(pos):
                self._focus_setup(index)
                return
            if layout["minus"][key].collidepoint(pos):
                self._focus_setup(index)
                self._nudge(-1)
                return
            if layout["plus"][key].collidepoint(pos):
                self._focus_setup(index)
                self._nudge(1)
                return
        for rect, sec in layout["durs"]:
            if rect.collidepoint(pos):
                self._commit_draft()
                self._set_profile_value("duration_s", sec)
                return
        if layout["go"].collidepoint(pos):
            self._activate()
            return
        self._commit_draft()

    def _nudge(self, sign: int) -> None:
        if self.state == "setup":
            self._commit_draft()
            key = self.setup_fields[self.field]
            steps = {"dpi": 50, "sensitivity": 0.01, "ads_multiplier": 0.05}
            self._set_profile_value(key, float(self.profile[key]) + sign * steps[key])
        elif self.state == "editor":
            self._edit_value(sign)

    def _backspace(self) -> None:
        if self.state != "setup":
            return
        if self.replace_next:
            self.draft = ""
            self.replace_next = False
            return
        self.draft = self.draft[:-1]

    def _type_setup(self, char: str) -> None:
        if self.state != "setup" or not char:
            return
        key = self.setup_fields[self.field]
        if char.isdigit() or (char == "." and key != "dpi"):
            if self.replace_next:
                self.draft = ""
                self.replace_next = False
            if char == "." and "." in self.draft:
                return
            if key == "dpi" and len(self.draft) >= 5:
                return
            if key != "dpi" and len(self.draft) >= 6:
                return
            self.draft += char

    def _type_hex(self, char: str) -> None:
        if self.state != "editor" or self.editor_i != 0 or not char:
            return
        if char == "#":
            self.hex_buf = ""
            return
        if char.lower() not in "0123456789abcdef":
            return
        self.hex_buf = (self.hex_buf + char.lower())[-6:]
        if len(self.hex_buf) == 6:
            self.crosshair["color"] = "#" + self.hex_buf.upper()
            self.crosshair["name"] = "custom"
            self.hex_buf = ""
            self._rebuild_crosshair()

    def _activate(self) -> None:
        if self.state == "setup":
            self._commit_draft()
            if float(self.profile["dpi"]) < 50 or float(self.profile["sensitivity"]) <= 0:
                self.message = "DPI mínimo 50 e sensibilidade maior que 0."
                return
            self.message = ""
            self.field = 0
            self.state = "menu"
        elif self.state == "menu":
            self._start_menu_index(self.field)
        elif self.state == "report":
            self.state = "menu"

    def _start_menu_index(self, index: int) -> None:
        actions = ["battery", "micro_flicks", "strafe_tracking", "editor", "report", "setup"]
        choice = actions[index]
        if choice == "editor":
            self.editor_i = 0
            self.state = "editor"
        elif choice == "report":
            self._open_report()
        elif choice == "setup":
            self.state = "setup"
        elif choice == "battery":
            self.battery_id = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
            self.queue = ["strafe_tracking"]
            self._begin("micro_flicks")
        else:
            self.battery_id = None
            self.queue = []
            self._begin(choice)

    def _menu_click(self, pos: tuple[int, int]) -> None:
        for index in range(6):
            rect = pygame.Rect(460, 230 + index * 64, 680, 52)
            if rect.collidepoint(pos):
                self.field = index
                self._start_menu_index(index)

    def _begin(self, exercise: str) -> None:
        self.cam.reset()
        self.play_t = 0.0
        self.fps_samples = []
        self.exercise = MicroFlicks(self.cam) if exercise == "micro_flicks" else StrafeTracking(self.cam)
        self.session = new_session(
            exercise=exercise,
            dpi=float(self.profile["dpi"]),
            sensitivity=float(self.profile["sensitivity"]),
            ads_multiplier=float(self.profile["ads_multiplier"]),
            duration_s=float(self.profile["duration_s"]),
            width=WIDTH,
            height=HEIGHT,
            battery_id=self.battery_id,
        )
        self.state = "play"
        self._set_grab(True)

    def _play(self, dt: float, clicked: bool) -> None:
        dx, dy = pygame.mouse.get_rel()
        self.cam.look(dx, dy, self._sens(), dt)
        self.play_t += dt
        self.fps_samples.append(self.clock.get_fps())
        assert self.exercise is not None
        self.exercise.update(self.play_t * 1000.0, dt, clicked)
        self.weapon.update(dt)
        if clicked:
            self.weapon.kick()
        if self.exercise.just_hit and self.hit_sound is not None:
            self.hit_sound.play()
        if self.play_t >= float(self.profile["duration_s"]):
            self._finish_exercise()

    def _finish_exercise(self) -> None:
        self._set_grab(False)
        if self.session is not None and self.exercise is not None:
            fps = sum(self.fps_samples) / len(self.fps_samples) if self.fps_samples else 0.0
            self.session["shots"] = list(self.exercise.shots)
            if isinstance(self.exercise, StrafeTracking):
                self.session.update(self.exercise.summary())
            close_session(self.session, fps)
            write_session(ROOT, self.session)
        self.exercise = None
        self.session = None
        if self.queue:
            nxt = self.queue.pop(0)
            self._begin(nxt)
            return
        self._open_report()

    def _open_report(self) -> None:
        sessions = load_sessions(sessions_dir(ROOT))
        result = analyze_sessions(sessions, float(self.profile["dpi"]), float(self.profile["sensitivity"]))
        out = ROOT / "data" / "recommendation.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.report = result["report"]
        self.state = "report"

    def _editor_rows(self) -> list[tuple[str, str]]:
        c = self.crosshair
        return [
            ("Cor", str(c.get("color"))),
            ("Contorno", "ligado" if c.get("outline_enabled") else "desligado"),
            ("Opacidade do contorno", f"{float(c.get('outline_opacity', 0)):.2f}"),
            ("Espessura do contorno", str(int(c.get("outline_thickness", 1)))),
            ("Ponto central", "ligado" if c.get("center_dot") else "desligado"),
            ("Opacidade do ponto", f"{float(c.get('center_dot_opacity', 1)):.2f}"),
            ("Espessura do ponto", str(int(c.get("center_dot_thickness", 2)))),
            ("Linhas internas", "ligadas" if c.get("inner_enabled", True) else "desligadas"),
            ("Opacidade interna", f"{float(c.get('inner_opacity', 1)):.2f}"),
            ("Comprimento interno", str(int(c.get("inner_length", 6)))),
            ("Espessura interna", str(int(c.get("inner_thickness", 2)))),
            ("Offset interno", str(int(c.get("inner_offset", 3)))),
            ("Linhas externas", "ligadas" if c.get("outer_enabled", True) else "desligadas"),
            ("Opacidade externa", f"{float(c.get('outer_opacity', 0)):.2f}"),
            ("Comprimento externo", str(int(c.get("outer_length", 2)))),
            ("Espessura externa", str(int(c.get("outer_thickness", 2)))),
            ("Offset externo", str(int(c.get("outer_offset", 10)))),
            ("Perfil", str(c.get("name", "custom"))),
        ]

    def _edit_value(self, sign: int) -> None:
        row = self.editor_i
        c = self.crosshair
        if row == 0:
            hexes = [h for _, h in COLORS]
            cur = str(c.get("color", "#00FF00")).upper()
            idx = 0
            for i, hx in enumerate(hexes):
                if hx.upper() == cur:
                    idx = i
            c["color"] = hexes[(idx + sign) % len(hexes)]
            c["name"] = "custom"
        elif row in (1, 4, 7, 12):
            key = ["outline_enabled", "center_dot", "inner_enabled", "outer_enabled"][{1: 0, 4: 1, 7: 2, 12: 3}[row]]
            c[key] = not bool(c.get(key))
        elif row == 17:
            names = list(presets())
            cur = str(c.get("name", "classica"))
            idx = names.index(cur) if cur in names else -1
            picked = presets()[names[(idx + sign) % len(names)]]
            self.crosshair = dict(picked)
            self._rebuild_crosshair()
            return
        else:
            keys = {
                2: ("outline_opacity", 0.05, 0.0, 1.0, 2),
                3: ("outline_thickness", 1, 0, 6, 0),
                5: ("center_dot_opacity", 0.05, 0.0, 1.0, 2),
                6: ("center_dot_thickness", 1, 1, 8, 0),
                8: ("inner_opacity", 0.05, 0.0, 1.0, 2),
                9: ("inner_length", 1, 0, 20, 0),
                10: ("inner_thickness", 1, 1, 8, 0),
                11: ("inner_offset", 1, 0, 30, 0),
                13: ("outer_opacity", 0.05, 0.0, 1.0, 2),
                14: ("outer_length", 1, 0, 20, 0),
                15: ("outer_thickness", 1, 1, 8, 0),
                16: ("outer_offset", 1, 0, 40, 0),
            }
            key, step, lo, hi, digits = keys[row]
            value = float(c.get(key, 0)) + sign * step
            value = max(lo, min(hi, value))
            c[key] = round(value, digits) if digits else int(value)
            c["name"] = "custom"
        self._rebuild_crosshair()

    def _editor_click(self, pos: tuple[int, int]) -> None:
        for index in range(len(self._editor_rows())):
            rect = pygame.Rect(80, 118 + index * 40, 760, 36)
            if rect.collidepoint(pos):
                self.editor_i = index
                if pos[0] > 700:
                    self._edit_value(1 if pos[0] > 760 else -1)

    def _draw(self) -> None:
        if self.state == "play":
            self._draw_world()
            self.weapon.draw(self.screen)
            blit_center(self.screen, self.sprite)
            self._draw_hud()
            return
        self.screen.fill(BG)
        self._chrome()
        if self.state == "setup":
            self._draw_setup()
        elif self.state == "menu":
            self._draw_menu()
        elif self.state == "editor":
            self._draw_editor()
        elif self.state == "report":
            self._draw_report()

    def _draw_button(self, rect: pygame.Rect, label: str, selected: bool) -> None:
        pygame.draw.rect(self.screen, (48, 32, 36) if selected else PANEL, rect, border_radius=8)
        if selected:
            pygame.draw.rect(self.screen, ACCENT, rect, 2, border_radius=8)
        surf = self.font.render(label, True, TEXT)
        self.screen.blit(surf, surf.get_rect(center=rect.center))

    def _draw_setup(self) -> None:
        layout = self._setup_rects()
        dpi = float(self.profile["dpi"])
        sens = float(self.profile["sensitivity"])
        self.screen.blit(self.title.render("Calibrar", True, TEXT), (390, 78))
        self.screen.blit(self.small.render("Clique no valor e digite. O primeiro número substitui o que está no campo.", True, MUTED), (390, 128))

        self.screen.blit(self.small.render("DPI", True, MUTED), (390, 168))
        for rect, value in layout["chips"]:
            self._draw_button(rect, str(value), int(dpi) == value)
        for index, key in enumerate(self.setup_fields):
            rect = layout["fields"][key]
            active = index == self.field
            pygame.draw.rect(self.screen, (18, 20, 24), rect, border_radius=8)
            pygame.draw.rect(self.screen, ACCENT if active else (70, 76, 86), rect, 2, border_radius=8)
            shown = self.draft if active else self._format_field(key)
            self.screen.blit(self.font.render(shown or " ", True, TEXT), (rect.x + 16, rect.y + 14))
            self._draw_button(layout["minus"][key], "−", False)
            self._draw_button(layout["plus"][key], "+", False)

        self.screen.blit(self.small.render("Sensibilidade no Valorant", True, MUTED), (390, 376))
        self.screen.blit(self.small.render("ADS  (botão direito no treino)", True, MUTED), (390, 532))
        self.screen.blit(self.small.render("Duração", True, MUTED), (980, 532))
        for rect, sec in layout["durs"]:
            self._draw_button(rect, f"{sec}s", int(self.profile["duration_s"]) == sec)

        card = pygame.Rect(1040, 196, 360, 250)
        pygame.draw.rect(self.screen, PANEL, card, border_radius=12)
        self.screen.blit(self.small.render("Escala", True, MUTED), (1068, 216))
        if dpi >= 50 and sens > 0:
            self.screen.blit(self.title.render(f"{edpi(dpi, sens):.0f}", True, TEXT), (1068, 252))
            self.screen.blit(self.small.render("eDPI", True, MUTED), (1068, 298))
            self.screen.blit(self.title.render(f"{cm_per_360(dpi, sens):.1f}", True, GOOD), (1068, 336))
            self.screen.blit(self.small.render("cm para 360°", True, MUTED), (1068, 382))
        else:
            self.screen.blit(self.font.render("DPI ou sens inválidos", True, ACCENT), (1068, 300))
        if self.message:
            self.screen.blit(self.small.render(self.message, True, ACCENT), (390, 660))
        self._draw_button(layout["go"], "Continuar", False)
        self.screen.blit(self.small.render("− / +  ajustam DPI de 50 em 50 e a sens de 0,01. Enter também continua.", True, MUTED), (390, 780))

    def _draw_menu(self) -> None:
        self.screen.blit(self.title.render("O que treinar", True, TEXT), (460, 96))
        dpi = float(self.profile["dpi"])
        sens = float(self.profile["sensitivity"])
        sub = f"{sens:.3f} sens   {int(dpi)} DPI   {edpi(dpi, sens):.0f} eDPI   {cm_per_360(dpi, sens):.1f} cm/360"
        self.screen.blit(self.small.render(sub, True, MUTED), (460, 140))
        items = [
            "Bateria completa (micro + strafe)",
            "Micro-Flicks",
            "Head-Level Strafe Tracking",
            "Editor de mira",
            "Relatório e recomendação",
            "Voltar ao setup",
        ]
        for i, item in enumerate(items):
            rect = pygame.Rect(460, 230 + i * 64, 680, 52)
            pygame.draw.rect(self.screen, (48, 32, 36) if i == self.field else PANEL, rect, border_radius=8)
            self.screen.blit(self.font.render(item, True, TEXT), (rect.x + 18, rect.y + 12))

    def _draw_editor(self) -> None:
        self.screen.blit(self.title.render("Mira", True, TEXT), (80, 72))
        rows = self._editor_rows()
        for i, (label, value) in enumerate(rows):
            y = 118 + i * 40
            if y > HEIGHT - 40:
                break
            selected = i == self.editor_i
            pygame.draw.rect(self.screen, (40, 48, 58) if selected else PANEL, pygame.Rect(80, y, 760, 36), border_radius=6)
            self.screen.blit(self.small.render(label, True, TEXT), (96, y + 8))
            color = parse_hex(value) if label == "Cor" else TEXT
            self.screen.blit(self.small.render(value, True, color), (560, y + 8))
        preview = pygame.Surface((420, 420))
        preview.fill((12, 12, 12))
        blit_center(preview, self.sprite)
        self.screen.blit(preview, (980, 180))
        hint = "Esquerda/direita ajusta. Na cor, digite 6 dígitos HEX. Esc volta."
        self.screen.blit(self.small.render(hint, True, MUTED), (80, HEIGHT - 36))

    def _draw_report(self) -> None:
        self.screen.blit(self.title.render("Seu resultado", True, TEXT), (80, 78))
        y = 130
        for paragraph in self.report.split("\n"):
            if paragraph.startswith("Faça isto:"):
                for line in _wrap(self.font, paragraph, WIDTH - 160):
                    self.screen.blit(self.font.render(line, True, GOOD), (80, y))
                    y += 32
                y += 8
                continue
            font = self.small if paragraph.startswith("-") or paragraph.startswith("Confiança") or paragraph.startswith("Leitura") else self.font
            color = MUTED if paragraph.startswith("Sensibilidade") or paragraph.startswith("Por quê") else TEXT
            if not paragraph:
                y += 10
                continue
            for line in _wrap(font, paragraph, WIDTH - 160):
                self.screen.blit(font.render(line, True, color), (80, y))
                y += 28 if font is self.font else 24
        self.screen.blit(self.small.render("Enter volta ao menu.", True, MUTED), (80, HEIGHT - 40))

    def _chrome(self) -> None:
        pygame.draw.rect(self.screen, (12, 14, 18), pygame.Rect(0, 0, WIDTH, 52))
        pygame.draw.line(self.screen, (48, 32, 36), (0, 52), (WIDTH, 52), 1)
        self.screen.blit(self.title.render(APP, True, ACCENT), (28, 8))
        self.screen.blit(self.small.render("treino de mira local", True, MUTED), (108, 18))

    def _draw_world(self) -> None:
        sky = pygame.Surface((WIDTH, HEIGHT))
        for y in range(0, HEIGHT, 4):
            t = y / HEIGHT
            color = (int(22 + 8 * t), int(26 + 6 * t), int(34 + 4 * t))
            pygame.draw.rect(sky, color, pygame.Rect(0, y, WIDTH, 4))
        self.screen.blit(sky, (0, 0))
        wall = [
            project_point(self.cam.yaw, self.cam.pitch, self.cam.pos, p, WIDTH, HEIGHT)
            for p in ((-6, 0, 20), (6, 0, 20), (6, 3.2, 20), (-6, 3.2, 20))
        ]
        if all(wall):
            pygame.draw.polygon(self.screen, (28, 32, 38), [(p[0], p[1]) for p in wall if p])
        for x in range(-8, 9, 2):
            self._seg((x, 0.0, 3.0), (x, 0.0, 24.0), (48, 54, 62))
        for z in range(4, 26, 4):
            shade = 36 + z
            self._seg((-8.0, 0.0, z), (8.0, 0.0, z), (shade, shade + 4, shade + 8))
        assert self.exercise is not None
        pos, radius = self.exercise.target
        center = project_point(self.cam.yaw, self.cam.pitch, self.cam.pos, pos, WIDTH, HEIGHT)
        if center is None:
            return
        edge = project_point(self.cam.yaw, self.cam.pitch, self.cam.pos, (pos[0] + radius, pos[1], pos[2]), WIDTH, HEIGHT)
        px = 8
        if edge is not None:
            px = max(4, int(math.hypot(edge[0] - center[0], edge[1] - center[1])))
        spot = (int(center[0]), int(center[1]))
        pygame.draw.circle(self.screen, (90, 24, 32), spot, px + 3)
        pygame.draw.circle(self.screen, ACCENT, spot, px)
        pygame.draw.circle(self.screen, (255, 176, 168), (spot[0] - px // 3, spot[1] - px // 3), max(2, px // 3))

    def _seg(self, a: tuple[float, float, float], b: tuple[float, float, float], color: tuple[int, int, int]) -> None:
        pa = project_point(self.cam.yaw, self.cam.pitch, self.cam.pos, a, WIDTH, HEIGHT)
        pb = project_point(self.cam.yaw, self.cam.pitch, self.cam.pos, b, WIDTH, HEIGHT)
        if pa and pb:
            pygame.draw.line(self.screen, color, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), 1)

    def _draw_hud(self) -> None:
        assert self.exercise is not None
        left = max(0.0, float(self.profile["duration_s"]) - self.play_t)
        name = "Micro-Flicks" if self.exercise.name == "micro_flicks" else "Strafe Tracking"
        ads = "  ADS" if pygame.mouse.get_pressed(3)[2] else ""
        label = f"{APP}   {name}   {left:0.1f}s   sens {self._sens():.3f}{ads}   FOV {HORIZONTAL_FOV_DEG:.0f}"
        surf = self.small.render(label, True, TEXT)
        bg = pygame.Surface((surf.get_width() + 16, surf.get_height() + 8), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 140))
        self.screen.blit(bg, (12, 12))
        self.screen.blit(surf, (20, 16))


def main() -> None:
    App().run()
