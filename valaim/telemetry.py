"""Gravação local de sessões em JSON."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def sessions_dir(root: Path) -> Path:
    path = root / "data" / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_session(root: Path, payload: dict) -> Path:
    folder = sessions_dir(root)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    exercise = str(payload.get("exercise") or "session")
    path = folder / f"{stamp}-{exercise}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def new_session(
    *,
    exercise: str,
    dpi: float,
    sensitivity: float,
    ads_multiplier: float,
    duration_s: float,
    width: int,
    height: int,
    battery_id: Optional[str],
) -> dict:
    from valaim.mathutil import HORIZONTAL_FOV_DEG, VALORANT_YAW, cm_per_360, edpi

    return {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "exercise": exercise,
        "battery_id": battery_id,
        "dpi": dpi,
        "sensitivity": sensitivity,
        "ads_multiplier": ads_multiplier,
        "edpi": round(edpi(dpi, sensitivity), 3),
        "cm_per_360": round(cm_per_360(dpi, sensitivity), 3),
        "yaw_deg_per_count": VALORANT_YAW,
        "horizontal_fov_deg": HORIZONTAL_FOV_DEG,
        "duration_s": duration_s,
        "resolution": [width, height],
        "fps_avg": 0.0,
        "shots": [],
        "hits": 0,
        "misses": 0,
        "accuracy_pct": 0.0,
        "time_on_target_pct": None,
        "direction_reversal_lag_ms_mean": None,
        "bias": {"right": 0, "left": 0, "up": 0, "down": 0},
    }


def close_session(session: dict, fps_avg: float) -> dict:
    shots = session["shots"]
    clicks = [s for s in shots if "hit" in s]
    hits = sum(1 for s in clicks if s.get("hit"))
    misses = sum(1 for s in clicks if not s.get("hit"))
    session["hits"] = hits
    session["misses"] = misses
    session["accuracy_pct"] = round(100.0 * hits / len(clicks), 2) if clicks else 0.0
    session["fps_avg"] = round(fps_avg, 1)
    session["finished_at"] = datetime.now().isoformat(timespec="seconds")
    bias = {"right": 0, "left": 0, "up": 0, "down": 0}
    for shot in clicks:
        rx = float(shot.get("residual_x_deg") or 0.0)
        ry = float(shot.get("residual_y_deg") or 0.0)
        if abs(rx) >= 0.15:
            bias["right" if rx > 0 else "left"] += 1
        if abs(ry) >= 0.15:
            bias["up" if ry > 0 else "down"] += 1
    session["bias"] = bias
    return session
