"""Refinamiento local por mínimos cuadrados, sensibilidad e incertidumbre.

* Jacobiano por diferencias finitas en el espacio normalizado, evaluado en
  paralelo (una simulación por parámetro).
* ``least_squares`` (Trust Region Reflective, con límites) parte de la solución
  elegida del frente de Pareto: el NSGA-II encuentra la cuenca, el gradiente
  afina hasta el mínimo local (estrategia memética/híbrida).
* Sensibilidad compuesta escalada (CSS, Hill & Tiedeman 2007), matriz de
  correlación y desviación típica de los parámetros a partir de (JᵀJ)^-1 para
  detectar parámetros no identificables.
"""
from __future__ import annotations

from typing import Callable, List, Optional

import numpy as np
from scipy.optimize import least_squares


def jacobian(res_fn_batch: Callable[[List[np.ndarray]], List[np.ndarray]], u: np.ndarray,
             r0: Optional[np.ndarray] = None, h: float = 0.02):
    """Jacobiano dr/du por diferencias hacia delante (o atrás junto al límite)."""
    pts, steps = [], []
    for j in range(len(u)):
        du = h if u[j] + h <= 1 else -h
        v = u.copy()
        v[j] += du
        pts.append(v)
        steps.append(du)
    batch = ([u] if r0 is None else []) + pts
    R = res_fn_batch(batch)
    if r0 is None:
        r0, R = R[0], R[1:]
    J = np.column_stack([(R[j] - r0) / steps[j] for j in range(len(u))])
    return J, r0


def sensitivity_report(J: np.ndarray, r: np.ndarray, names: List[str]) -> dict:
    """CSS, desviación típica (espacio unitario) y correlaciones de parámetros."""
    nd = max(J.shape[0], 1)
    css = np.sqrt(np.sum(J ** 2, axis=0) / nd)
    dof = max(J.shape[0] - J.shape[1], 1)
    s2 = float(r @ r) / dof
    JTJ = J.T @ J
    try:
        cov = s2 * np.linalg.pinv(JTJ)
        sd = np.sqrt(np.clip(np.diag(cov), 0, None))
        dn = np.where(sd > 0, sd, 1)
        corr = cov / np.outer(dn, dn)
    except np.linalg.LinAlgError:
        sd = np.full(len(names), np.nan)
        corr = np.full((len(names), len(names)), np.nan)
    cmax = css.max() if css.size and css.max() > 0 else 1.0
    return {"names": names, "css": css.tolist(), "css_rel": (css / cmax).tolist(),
            "sd_unit": sd.tolist(), "corr": corr.tolist(), "s2": s2,
            "cond": float(np.linalg.cond(JTJ)) if JTJ.size else np.nan}


def refine(res_fn_batch: Callable[[List[np.ndarray]], List[np.ndarray]], u0: np.ndarray,
           max_iter: int = 15, h: float = 0.02, log: Callable[[str], None] = print,
           free: Optional[np.ndarray] = None):
    """Minimiza ||r(u)||² con límites [0,1] usando un jacobiano paralelo."""
    free = np.ones(len(u0), bool) if free is None else np.asarray(free, bool)
    idx = np.where(free)[0]
    if len(idx) == 0:
        return u0, None
    cache = {}

    def full(v):
        u = u0.copy()
        u[idx] = v
        return u

    def fun(v):
        key = tuple(np.round(v, 12))
        if key not in cache:
            cache[key] = res_fn_batch([full(v)])[0]
        return cache[key]

    def jac(v):
        r0 = fun(v)
        J, _ = jacobian(lambda B: res_fn_batch([full(b) for b in B]), v, r0, h)
        return J

    sol = least_squares(fun, u0[idx], jac=jac, bounds=(0.0, 1.0), method="trf",
                        x_scale="jac", max_nfev=max_iter, diff_step=h, verbose=0)
    log(f"  refinamiento local: coste {0.5 * np.sum(fun(u0[idx]) ** 2):.6g} -> "
        f"{sol.cost:.6g} ({sol.nfev} iteraciones, estado {sol.status})")
    return full(sol.x), sol
