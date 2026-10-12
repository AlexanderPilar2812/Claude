"""NSGA-II (Deb et al., 2002) vectorizado en NumPy, sobre el hipercubo [0,1]^d.

Mejoras respecto a una implementación básica:
  * población inicial por hipercubo latino + semillas (modelo original, etc.)
  * dominancia con restricciones (simulaciones que no convergen = no factibles)
  * eliminación de duplicados para no gastar simulaciones
  * archivo externo con todas las soluciones no dominadas encontradas
  * parada anticipada por estancamiento del hipervolumen
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np


# ------------------------------------------------------------------ utilidades
def dominates(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.all(a <= b) and np.any(a < b))


def fast_non_dominated_sort(F: np.ndarray, CV: Optional[np.ndarray] = None) -> List[np.ndarray]:
    """Frentes de Pareto (lista de arrays de índices). Con restricciones: una
    solución factible domina a cualquier no factible; entre no factibles gana la
    de menor violación."""
    n = len(F)
    if CV is None:
        CV = np.zeros(n)
    le = np.all(F[:, None, :] <= F[None, :, :], axis=2)
    lt = np.any(F[:, None, :] < F[None, :, :], axis=2)
    dom = le & lt                                   # dom[i,j]: i domina a j
    feas = CV <= 0
    both_feas = feas[:, None] & feas[None, :]
    cdom = np.where(both_feas, dom, False)
    cdom |= feas[:, None] & ~feas[None, :]
    both_inf = ~feas[:, None] & ~feas[None, :]
    cdom |= both_inf & (CV[:, None] < CV[None, :])
    n_dom = cdom.sum(axis=0)
    fronts = []
    current = np.where(n_dom == 0)[0]
    assigned = np.zeros(n, bool)
    while len(current):
        fronts.append(current)
        assigned[current] = True
        n_dom = n_dom - cdom[current].sum(axis=0)
        current = np.where((n_dom == 0) & ~assigned)[0]
    return fronts


def crowding_distance(F: np.ndarray) -> np.ndarray:
    n, m = F.shape
    if n <= 2:
        return np.full(n, np.inf)
    d = np.zeros(n)
    for j in range(m):
        order = np.argsort(F[:, j], kind="mergesort")
        f = F[order, j]
        span = f[-1] - f[0]
        d[order[0]] = d[order[-1]] = np.inf
        if span > 0:
            d[order[1:-1]] += (f[2:] - f[:-2]) / span
    return d


def hypervolume_2d(F: np.ndarray, ref: np.ndarray) -> float:
    P = F[np.all(F < ref, axis=1)]
    if len(P) == 0:
        return 0.0
    P = P[np.argsort(P[:, 0])]
    hv, best_y = 0.0, ref[1]
    for x, y in P:
        if y < best_y:
            hv += (ref[0] - x) * (best_y - y)
            best_y = y
    return hv


def hypervolume(F: np.ndarray, ref: np.ndarray, n_mc: int = 20000, seed: int = 0) -> float:
    """Exacto en 1-2 objetivos; Monte Carlo en 3+."""
    if F.shape[1] == 1:
        return float(max(0.0, ref[0] - F[:, 0].min()))
    if F.shape[1] == 2:
        return hypervolume_2d(F, ref)
    rng = np.random.default_rng(seed)
    lo = F.min(axis=0)
    pts = lo + rng.random((n_mc, F.shape[1])) * (ref - lo)
    dom = np.zeros(n_mc, bool)
    for f in F:
        dom |= np.all(pts >= f, axis=1)
    return float(dom.mean() * np.prod(ref - lo))


def lhs(n: int, d: int, rng) -> np.ndarray:
    cut = (np.arange(n)[:, None] + rng.random((n, d))) / n
    for j in range(d):
        cut[:, j] = cut[rng.permutation(n), j]
    return cut


def sbx(p1, p2, eta, pc, rng):
    c1, c2 = p1.copy(), p2.copy()
    if rng.random() > pc:
        return c1, c2
    for j in range(len(p1)):
        if rng.random() > 0.5 or abs(p1[j] - p2[j]) < 1e-14:
            continue
        y1, y2 = min(p1[j], p2[j]), max(p1[j], p2[j])
        u = rng.random()
        beta = 1.0 + 2.0 * y1 / (y2 - y1)
        alpha = 2.0 - beta ** -(eta + 1)
        bq = (u * alpha) ** (1 / (eta + 1)) if u <= 1 / alpha else (1 / (2 - u * alpha)) ** (1 / (eta + 1))
        a = 0.5 * ((y1 + y2) - bq * (y2 - y1))
        beta = 1.0 + 2.0 * (1 - y2) / (y2 - y1)
        alpha = 2.0 - beta ** -(eta + 1)
        bq = (u * alpha) ** (1 / (eta + 1)) if u <= 1 / alpha else (1 / (2 - u * alpha)) ** (1 / (eta + 1))
        b = 0.5 * ((y1 + y2) + bq * (y2 - y1))
        a, b = np.clip([a, b], 0, 1)
        if rng.random() < 0.5:
            a, b = b, a
        c1[j], c2[j] = a, b
    return c1, c2


def poly_mutation(x, eta, pm, rng):
    y = x.copy()
    for j in range(len(x)):
        if rng.random() >= pm:
            continue
        u = rng.random()
        d1, d2 = y[j], 1 - y[j]
        mp = 1 / (eta + 1)
        if u < 0.5:
            val = 2 * u + (1 - 2 * u) * (1 - d1) ** (eta + 1)
            dq = val ** mp - 1
        else:
            val = 2 * (1 - u) + 2 * (u - 0.5) * (1 - d2) ** (eta + 1)
            dq = 1 - val ** mp
        y[j] = np.clip(y[j] + dq, 0, 1)
    return y


# ---------------------------------------------------------------------- NSGA-II
@dataclass
class NSGA2Result:
    X: np.ndarray            # frente final (espacio unitario)
    F: np.ndarray
    archive_X: np.ndarray    # todas las evaluaciones
    archive_F: np.ndarray
    archive_CV: np.ndarray
    history: List[dict] = field(default_factory=list)
    n_evals: int = 0


def nsga2(evaluate: Callable[[np.ndarray], tuple], n_var: int, pop_size: int = 60,
          n_gen: int = 60, seeds: Optional[np.ndarray] = None, eta_c: float = 15,
          eta_m: float = 20, pc: float = 0.9, pm: Optional[float] = None,
          seed: int = 1, stall_gens: int = 15, stall_tol: float = 1e-4,
          max_evals: Optional[int] = None, log: Callable[[str], None] = print) -> NSGA2Result:
    """``evaluate(U) -> (F[n, m], CV[n])`` evalúa un lote de individuos."""
    rng = np.random.default_rng(seed)
    pm = 1.0 / n_var if pm is None else pm
    pop_size += pop_size % 2
    X = lhs(pop_size, n_var, rng)
    if seeds is not None and len(seeds):
        k = min(len(seeds), pop_size // 2)
        X[:k] = np.clip(np.asarray(seeds)[:k], 0, 1)
    F, CV = evaluate(X)
    arch_X, arch_F, arch_CV = [X], [F], [CV]
    n_evals = len(X)
    history, best_hv, stall = [], -np.inf, 0
    ref = None
    t0 = time.time()

    def rank_crowd(F_, CV_):
        fronts = fast_non_dominated_sort(F_, CV_)
        rank = np.empty(len(F_), int)
        crowd = np.empty(len(F_))
        for r, fr in enumerate(fronts):
            rank[fr] = r
            crowd[fr] = crowding_distance(F_[fr])
        return rank, crowd, fronts

    rank, crowd, _ = rank_crowd(F, CV)
    for gen in range(1, n_gen + 1):
        # selección por torneo binario (rango, luego distancia de crowding)
        def tour():
            a, b = rng.integers(0, len(X), 2)
            if rank[a] != rank[b]:
                return a if rank[a] < rank[b] else b
            return a if crowd[a] > crowd[b] else b

        children = []
        seen = {tuple(np.round(x, 6)) for x in X}
        tries = 0
        while len(children) < pop_size and tries < pop_size * 20:
            tries += 1
            c1, c2 = sbx(X[tour()], X[tour()], eta_c, pc, rng)
            for c in (poly_mutation(c1, eta_m, pm, rng), poly_mutation(c2, eta_m, pm, rng)):
                key = tuple(np.round(c, 6))
                if key not in seen:
                    seen.add(key)
                    children.append(c)
        Q = np.array(children[:pop_size])
        FQ, CVQ = evaluate(Q)
        n_evals += len(Q)
        arch_X.append(Q), arch_F.append(FQ), arch_CV.append(CVQ)
        # selección ambiental (mu + lambda)
        RX, RF, RCV = np.vstack([X, Q]), np.vstack([F, FQ]), np.concatenate([CV, CVQ])
        fronts = fast_non_dominated_sort(RF, RCV)
        keep = []
        for fr in fronts:
            if len(keep) + len(fr) <= pop_size:
                keep.extend(fr)
            else:
                cd = crowding_distance(RF[fr])
                keep.extend(fr[np.argsort(-cd)][: pop_size - len(keep)])
                break
        keep = np.array(keep)
        X, F, CV = RX[keep], RF[keep], RCV[keep]
        rank, crowd, fr0 = rank_crowd(F, CV)
        front = F[fr0[0]][CV[fr0[0]] <= 0]
        if ref is None and len(front):
            ref = np.max(np.vstack(arch_F)[np.concatenate(arch_CV) <= 0], axis=0) * 1.1 + 1e-12
        hv = hypervolume(front, ref) if (ref is not None and len(front)) else 0.0
        best = front.min(axis=0) if len(front) else np.full(F.shape[1], np.nan)
        history.append({"gen": gen, "evals": n_evals, "hv": hv, "front_size": len(front),
                        "best": best.tolist(), "time": time.time() - t0})
        log(f"  gen {gen:3d} | evals {n_evals:5d} | frente {len(front):3d} | HV {hv:.5g} | "
            f"mejor por objetivo {np.array2string(best, precision=4)}")
        if hv > best_hv * (1 + stall_tol) + 1e-15:
            best_hv, stall = hv, 0
        else:
            stall += 1
        if stall >= stall_gens:
            log(f"  parada por estancamiento del hipervolumen ({stall_gens} generaciones)")
            break
        if max_evals and n_evals >= max_evals:
            log("  parada por número máximo de evaluaciones")
            break

    AX, AF, ACV = np.vstack(arch_X), np.vstack(arch_F), np.concatenate(arch_CV)
    # frente no dominado global del archivo (puede mejorar al de la última población)
    fr = fast_non_dominated_sort(AF, ACV)[0] if len(AF) <= 6000 else \
        fast_non_dominated_sort(F, CV)[0]
    src_X, src_F = (AX, AF) if len(AF) <= 6000 else (X, F)
    fX, fF = src_X[fr], src_F[fr]
    _, uniq = np.unique(np.round(fF, 10), axis=0, return_index=True)
    return NSGA2Result(X=fX[uniq], F=fF[uniq], archive_X=AX, archive_F=AF, archive_CV=ACV,
                       history=history, n_evals=n_evals)
