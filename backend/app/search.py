"""Bounded refinement uses tuning windows only, never final CV answers."""

import math


def refine_ridge(trials, lower, upper):
    successful = [t for t in trials if t.get("score") is not None]
    if not successful:
        return None
    best = min(successful, key=lambda t: t["score"])["parameters"]["alpha"]
    seen = sorted({t["parameters"]["alpha"] for t in trials})
    position = seen.index(best)
    neighbours = ([seen[position - 1]] if position else []) + (
        [seen[position + 1]] if position + 1 < len(seen) else []
    )
    # Refine the wider log-space side, within the explicitly configured bounds.
    for neighbour in sorted(neighbours, key=lambda a: -abs(math.log(a / best))):
        proposal = math.sqrt(neighbour * best)
        if lower <= proposal <= upper and not any(math.isclose(proposal, a) for a in seen):
            return {"alpha": proposal}
    return None


def choose_fittable(rows, features, horizon, family, ranked, fit_ends, seed, job_id, deadline):
    from .research import fit, checkpoint, Cancelled, BudgetExceeded

    failures = []
    # The ranked list was fixed on tuning data. Preflight sees fitting prefixes
    # only; a failed metric or low final matching score NEVER triggers fallback.
    for settings in ranked[:3]:
        artifacts = []
        try:
            for end in fit_ends:
                checkpoint(job_id, f"拟合预检 · {family}", deadline=deadline)
                artifacts.append(fit(rows, features, end, horizon, family, settings["parameters"], seed))
            return settings, artifacts, failures
        except (Cancelled, BudgetExceeded):
            raise
        except Exception as exc:
            failures.append({"parameters": settings["parameters"], "error": str(exc)[:300]})
    raise ValueError("前三个调参候选均未通过拟合预检：" + str(failures)[:800])
