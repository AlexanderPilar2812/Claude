"""Elección de la solución de compromiso dentro del frente de Pareto."""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np


def _normalize(F: np.ndarray) -> np.ndarray:
    lo, hi = F.min(axis=0), F.max(axis=0)
    span = np.where(hi - lo > 0, hi - lo, 1.0)
    return (F - lo) / span


def knee_point(F: np.ndarray) -> int:
    """Punto de máxima convexidad (distancia mínima al punto ideal normalizado,
    ponderada por la distancia al hiperplano de los extremos)."""
    if len(F) == 1:
        return 0
    N = _normalize(F)
    # distancia al hiperplano sum(N)=1 que une los extremos (mayor = más "codo")
    plane = (1.0 - N.sum(axis=1)) / np.sqrt(F.shape[1])
    ideal = np.linalg.norm(N, axis=1)
    score = plane - 0.25 * ideal
    return int(np.argmax(score))


def topsis(F: np.ndarray, weights: Optional[Sequence[float]] = None) -> int:
    w = np.ones(F.shape[1]) if weights is None else np.asarray(weights, float)
    w = w / w.sum()
    N = F / np.where(np.linalg.norm(F, axis=0) > 0, np.linalg.norm(F, axis=0), 1)
    V = N * w
    best, worst = V.min(axis=0), V.max(axis=0)
    dp = np.linalg.norm(V - best, axis=1)
    dn = np.linalg.norm(V - worst, axis=1)
    return int(np.argmax(dn / np.where(dp + dn > 0, dp + dn, 1)))


def weighted(F: np.ndarray, weights: Optional[Sequence[float]] = None) -> int:
    w = np.ones(F.shape[1]) if weights is None else np.asarray(weights, float)
    return int(np.argmin(_normalize(F) @ w))


def choose(F: np.ndarray, method: str = "knee", weights=None,
           rules_passed: Optional[np.ndarray] = None,
           rules_score: Optional[np.ndarray] = None) -> int:
    """Primero se priorizan las soluciones que cumplen más reglas de aceptación;
    entre ellas se aplica el criterio multicriterio elegido."""
    cand = np.arange(len(F))
    if rules_passed is not None:
        cand = cand[rules_passed == rules_passed.max()]
        if rules_score is not None and len(cand) > 1:
            sc = rules_score[cand]
            cand = cand[sc >= sc.max() - 0.02]  # empate técnico (2 %)
    sub = F[cand]
    if method == "topsis":
        k = topsis(sub, weights)
    elif method == "weighted":
        k = weighted(sub, weights)
    else:
        k = knee_point(sub)
    return int(cand[k])
