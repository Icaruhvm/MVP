"""Calibração local de sensibilidade. Sem rede e sem modelo treinado externo.

A decisão firme exige 3 baterias completas (micro + strafe no mesmo battery_id).
Com menos dados o texto deixa claro que a leitura ainda é provisória.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Iterable, Optional

from valaim.mathutil import cm_per_360, edpi


def _mean(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return float(statistics.fmean(values))


def _pct(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return 100.0 * part / whole


def load_sessions(directory: Path) -> list[dict]:
    sessions = []
    if not directory.exists():
        return sessions
    for path in sorted(directory.glob("*.json")):
        try:
            sessions.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sessions


def suggest_ab_ladder(sensitivity: float) -> list[dict]:
    """Escada PSA: variações controladas em torno da sens atual."""
    steps = [-0.10, 0.0, 0.10, -0.05, 0.05, -0.02, 0.02]
    ladder = []
    for factor in steps:
        sens = round(max(0.01, sensitivity * (1.0 + factor)), 3)
        ladder.append({"factor": factor, "sensitivity": sens, "label": f"{factor:+.0%}"})
    return ladder


def _shots(session: dict, exercise: Optional[str] = None) -> list[dict]:
    if exercise is not None and session.get("exercise") != exercise:
        return []
    return list(session.get("shots") or [])


def _batteries(sessions: Iterable[dict]) -> dict[str, dict[str, dict]]:
    found: dict[str, dict[str, dict]] = {}
    for session in sessions:
        bid = session.get("battery_id")
        kind = session.get("exercise")
        if not bid or kind not in ("micro_flicks", "strafe_tracking", "wide_flicks", "reaction"):
            continue
        found.setdefault(str(bid), {})[kind] = session
    return found


def _complete_batteries(sessions: list[dict], sensitivity: float) -> list[dict[str, dict]]:
    done = []
    for kinds in _batteries(sessions).values():
        micro = kinds.get("micro_flicks")
        strafe = kinds.get("strafe_tracking")
        if not micro or not strafe:
            continue
        if abs(float(micro.get("sensitivity", -1)) - sensitivity) > 0.00051:
            continue
        if abs(float(strafe.get("sensitivity", -1)) - sensitivity) > 0.00051:
            continue
        done.append(kinds)
    return done


def _collect(batteries: list[dict[str, dict]], exercise: str, field: str, pred=None) -> list[float]:
    values = []
    for kinds in batteries:
        session = kinds.get(exercise)
        if not session:
            continue
        for shot in session.get("shots") or []:
            if pred is not None and not pred(shot):
                continue
            if field not in shot or shot[field] is None:
                continue
            values.append(float(shot[field]))
    return values


def analyze_sessions(sessions: list[dict], dpi: float, sensitivity: float) -> dict:
    """Gera diagnóstico e um ajuste percentual sugerido."""
    batteries = _complete_batteries(sessions, sensitivity)
    validated = len(batteries) >= 3
    # Sem 3 baterias, ainda lê o histórico dessa sens para um parecer provisório.
    pool = batteries
    if not pool:
        loose: dict[str, dict] = {}
        for session in sessions:
            if abs(float(session.get("sensitivity", -1)) - sensitivity) > 0.00051:
                continue
            kind = session.get("exercise")
            if kind:
                loose[kind] = session
        if loose:
            pool = [loose]

    micro_over = _collect(pool, "micro_flicks", "overshoot_deg")
    micro_under = _collect(pool, "micro_flicks", "undershoot_deg")
    micro_eff = _collect(pool, "micro_flicks", "path_efficiency")
    micro_rev = _collect(pool, "micro_flicks", "near_target_reversals")
    micro_hits = [
        1.0 if s.get("hit") else 0.0
        for kinds in pool
        for s in (kinds.get("micro_flicks") or {}).get("shots") or []
    ]
    wide_over = _collect(pool, "wide_flicks", "overshoot_deg", lambda s: float(s.get("initial_angle_deg") or 0) >= 30.0)
    wide_under = _collect(pool, "wide_flicks", "undershoot_deg", lambda s: float(s.get("initial_angle_deg") or 0) >= 30.0)
    wide_hits = [
        1.0 if s.get("hit") else 0.0
        for kinds in pool
        for s in (kinds.get("wide_flicks") or {}).get("shots") or []
        if float(s.get("initial_angle_deg") or 0) >= 30.0
    ]
    strafe_lag = []
    strafe_tot = []
    for kinds in pool:
        st = kinds.get("strafe_tracking") or {}
        if st.get("direction_reversal_lag_ms_mean") is not None:
            strafe_lag.append(float(st["direction_reversal_lag_ms_mean"]))
        if st.get("time_on_target_pct") is not None:
            strafe_tot.append(float(st["time_on_target_pct"]))

    m_over = _mean(micro_over) or 0.0
    m_under = _mean(micro_under) or 0.0
    m_eff = _mean(micro_eff) if micro_eff else None
    m_rev = _mean(micro_rev) or 0.0
    m_acc = _pct(int(sum(micro_hits)), len(micro_hits)) if micro_hits else None
    w_over = _mean(wide_over)
    w_under = _mean(wide_under)
    w_acc = _pct(int(sum(wide_hits)), len(wide_hits)) if wide_hits else None
    s_lag = _mean(strafe_lag)
    s_tot = _mean(strafe_tot)

    # Proxy quando o exercício de wide flick ainda não existe: overshoot do micro.
    overshoot_signal = w_over if w_over is not None else m_over
    undershoot_signal = w_under if w_under is not None else m_under
    wide_precise = (w_acc is not None and w_acc >= 70.0) or (w_acc is None and (m_acc or 0) >= 75.0)

    adjust = 0.0
    reasons: list[str] = []
    grip_note = False

    high_over = overshoot_signal >= 0.85
    low_eff = m_eff is not None and m_eff < 0.75
    high_under = undershoot_signal >= 0.85
    # Atraso só conta se a mira também ficou pouco tempo no alvo.
    # Com tracking já bom, um lag alto é falha do medidor, não sens baixa.
    slow_reverse = s_lag is not None and s_lag >= 180.0 and (s_tot is None or s_tot < 50.0)
    tremor = m_rev >= 3.0 and (m_eff is None or m_eff < 0.88)
    already_good = (m_acc or 0.0) >= 65.0 and (s_tot or 0.0) >= 55.0 and overshoot_signal < 0.6

    if already_good and not (high_over and low_eff):
        adjust = 0.0
        reasons.append("acerto no micro e tempo no alvo do tracking já estão bons; não há motivo para mudar a escala")
    elif high_over and low_eff and not (high_under and slow_reverse):
        severity = min(1.0, ((overshoot_signal - 0.85) / 1.6 + (0.75 - (m_eff or 0.75)) / 0.25) / 2)
        adjust = -round(3.0 + severity * 5.0, 1)
        reasons.append(
            "overshoot alto em giros"
            + (" largos" if w_over is not None else " (proxy: micro-flicks)")
            + " com trajetória pouco eficiente nos micro-flicks"
        )
    elif high_under and slow_reverse and not (high_over and low_eff):
        severity = min(1.0, ((undershoot_signal - 0.85) / 1.6 + ((s_lag or 110) - 110) / 180) / 2)
        adjust = round(3.0 + severity * 5.0, 1)
        reasons.append("undershoot alto e atraso para inverter a direção no strafe")
    elif wide_precise and tremor and abs(overshoot_signal) < 1.4:
        # Tremor no fim do micro com flick largo já decente: ajuste fino para baixo
        # se houver overshoot residual, senão só tensionamento de pegada.
        if overshoot_signal >= 0.35:
            adjust = -round(1.0 + min(2.0, overshoot_signal), 1)
            reasons.append("precisão boa no deslocamento, mas tremor nos últimos milímetros do micro-flick")
        else:
            adjust = 0.0
            grip_note = True
            reasons.append("tremor nos últimos milímetros com a escala angular já próxima do alvo")
    elif high_over and high_under:
        reasons.append("sinais mistos de overshoot e undershoot; a sensibilidade sozinha não explica o padrão")
    else:
        reasons.append("métricas dentro da faixa estável para esta sensibilidade")

    new_sens = round(max(0.01, sensitivity * (1.0 + adjust / 100.0)), 3)
    # Evita recomendar a mesma sens arredondada quando o percentual é pequeno demais.
    if adjust != 0.0 and abs(new_sens - sensitivity) < 0.001:
        new_sens = round(sensitivity + (-0.001 if adjust < 0 else 0.001), 3)

    return {
        "validated": validated,
        "batteries": len(batteries),
        "sensitivity": sensitivity,
        "recommended_sensitivity": new_sens,
        "adjust_percent": adjust,
        "grip_tension": grip_note,
        "reasons": reasons,
        "metrics": {
            "micro_overshoot_deg": None if not micro_over else round(m_over, 3),
            "micro_undershoot_deg": None if not micro_under else round(m_under, 3),
            "micro_path_efficiency": None if m_eff is None else round(m_eff, 3),
            "micro_accuracy_pct": None if m_acc is None else round(m_acc, 1),
            "micro_near_reversals": None if not micro_rev else round(m_rev, 2),
            "wide_overshoot_deg": None if w_over is None else round(w_over, 3),
            "wide_undershoot_deg": None if w_under is None else round(w_under, 3),
            "wide_accuracy_pct": None if w_acc is None else round(w_acc, 1),
            "strafe_reversal_lag_ms": None if s_lag is None else round(s_lag, 1),
            "strafe_time_on_target_pct": None if s_tot is None else round(s_tot, 1),
            "micro_shots": len(micro_hits),
        },
        "ab_ladder": suggest_ab_ladder(sensitivity if adjust == 0 else new_sens),
        "report": _report(
            dpi,
            sensitivity,
            new_sens,
            adjust,
            validated,
            len(batteries),
            reasons,
            grip_note,
            m_acc,
            m_over,
            m_under,
            m_eff,
            s_lag,
            s_tot,
            w_over,
            w_under,
        ),
    }


def _fmt(value: Optional[float], suffix: str, digits: int = 1) -> str:
    if value is None:
        return "sem dados"
    return f"{value:.{digits}f}{suffix}"


def _report(
    dpi: float,
    sensitivity: float,
    new_sens: float,
    adjust: float,
    validated: bool,
    batteries: int,
    reasons: list[str],
    grip_note: bool,
    m_acc: Optional[float],
    m_over: float,
    m_under: float,
    m_eff: Optional[float],
    s_lag: Optional[float],
    s_tot: Optional[float],
    w_over: Optional[float],
    w_under: Optional[float],
) -> str:
    e = edpi(dpi, sensitivity)
    cm = cm_per_360(dpi, sensitivity)
    because = "; ".join(reasons)
    wide_line = ""
    if w_over is not None or w_under is not None:
        wide_line = (
            f"- Nos giros largos: passou do alvo {_fmt(w_over, ' graus', 2)}, "
            f"parou antes {_fmt(w_under, ' graus', 2)}."
        )
    if adjust == 0.0 and grip_note:
        action = "Mantenha esta sensibilidade. Segure o mouse com menos força nos últimos milímetros."
    elif adjust == 0.0:
        action = "Mantenha esta sensibilidade e repita o treino antes de mudar."
    elif adjust < 0:
        action = (
            f"Baixe para {new_sens:.3f} ({abs(adjust):.1f}% a menos, {edpi(dpi, new_sens):.0f} eDPI). "
            "A mira está passando do alvo."
        )
    else:
        action = (
            f"Suba para {new_sens:.3f} ({abs(adjust):.1f}% a mais, {edpi(dpi, new_sens):.0f} eDPI). "
            "A mira está parando antes do alvo."
        )
    straight = "sem dados" if m_eff is None else f"{m_eff * 100:.0f}%"
    status = (
        f"Confiança boa: {batteries} treinos completos nesta sensibilidade."
        if validated
        else (
            f"Leitura provisória: {batteries} de 3 treinos completos. "
            "Falta repetir para separar um dia ruim do seu padrão real."
        )
    )
    return "\n".join(
        [
            f"Faça isto: {action}",
            "",
            f"Sensibilidade atual: {sensitivity:.3f} ({e:.0f} eDPI, {cm:.1f} cm para girar 360°).",
            f"Por quê: {because}.",
            "",
            "Seus números, em palavras simples:",
            f"- Acertos no micro-flick: {_fmt(m_acc, '%')}.",
            f"- Passou do alvo: {_fmt(m_over, ' graus', 2)}. A mira atravessou o centro.",
            f"- Parou antes do alvo: {_fmt(m_under, ' graus', 2)}. Faltou chegar no centro.",
            f"- Movimento em linha reta: {straight}. 100% seria um gesto reto, sem tremor.",
            f"- Tempo com a mira em cima do alvo no tracking: {_fmt(s_tot, '%')}.",
            f"- Tempo para virar junto com o alvo: {_fmt(s_lag, ' ms', 0)}.",
            *(
                ["Os números de 'parou antes' e 'tempo para virar' não mudam a decisão quando o acerto e o tempo no alvo já estão bons."]
                if "já estão bons" in because
                else []
            ),
            *([wide_line] if wide_line else []),
            "",
            status,
        ]
    )
