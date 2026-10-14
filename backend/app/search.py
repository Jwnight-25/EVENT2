"""Bounded refinement uses tuning windows only, never final CV answers."""

import math
import json


def fingerprint(parameters):
    return json.dumps(parameters, sort_keys=True)


def refine_parameters(family, trials, config):
    successful = [t for t in trials if t.get("score") is not None]
    if not successful:
        return None
    best = min(successful, key=lambda t: t["score"])["parameters"]
    seen = {fingerprint(t["parameters"]) for t in trials}
    proposals = []
    if family == "ridge":
        lower, upper = config["ridge_alpha_min"], config["ridge_alpha_max"]
        peers = [t for t in trials if t["parameters"].get("feature_lags", 10) == best.get("feature_lags", 10)]
        middle = refine_ridge(peers, lower, upper)
        alphas = ([middle["alpha"]] if middle else []) + [
            best["alpha"] / math.sqrt(10),
            best["alpha"] * math.sqrt(10),
        ]
        values = [{**best, "alpha": float(a)} for a in alphas if lower <= a <= upper]
        structures = []
        if config.get("optimize_structure"):
            lag = best.get("feature_lags", 10)
            structures = [
                {**best, "feature_lags": v}
                for v in (lag - 1, lag + 1)
                if config["ridge_lags_min"] <= v <= config["ridge_lags_max"]
            ]
        proposals = structures + values if len(trials) % 2 else values + structures
    elif family in ("arima", "sarima", "garch"):
        order = best["order"]
        for index in (0, 2, 1):
            for change in (-1, 1):
                modified = list(order)
                modified[index] += change
                limit = 1 if index == 1 else config.get("arima_max_order", 2)
                if 0 <= modified[index] <= limit:
                    proposals.append({**best, "order": modified})
        if family == "sarima":
            seasonal = best["seasonal_order"]
            for index in (0, 2):
                modified = list(seasonal)
                modified[index] = 1 - modified[index]
                if modified[0] + modified[2] > 0:
                    proposals.insert(0, {**best, "seasonal_order": modified})
            proposals.append({**best, "seasonal_order": [*seasonal[:3], 20 if seasonal[3] == 5 else 5]})
        if family == "garch":
            proposals += [{**best, key: 3 - best[key]} for key in ("p", "q")]
    elif family == "lightgbm":
        for key, options in {
            "num_leaves": (7, 15, 31),
            "n_estimators": (40, 80, 160, 240),
            "learning_rate": (0.01, 0.02, 0.04, 0.08),
        }.items():
            proposals.extend({**best, key: v} for v in sorted(options, key=lambda v: abs(v - best[key])))
    elif family in ("nhits", "patchtst"):
        options = {
            "input_size": (60, 120),
            "max_steps": (100, 200, 300),
            "learning_rate": (0.0005, 0.001, 0.003),
        }
        if config.get("optimize_structure"):
            options.update({"hidden_size": (16, 32, 64), "hidden_layers": (1, 2, 3)})
        for key, values in options.items():
            proposals.extend({**best, key: v} for v in values if v != best.get(key))
    return next((p for p in proposals if fingerprint(p) not in seen), None)


def proposal_stream(families, grids, histories, counts, bests, config):
    """Reserve trials for feedback-driven refinement, even in the daily 4-trial budget."""
    cap = config.get("trials_per_family") or config["max_trials"]
    adaptive = config.get("search_profile") == "adaptive"
    coarse = {
        f: min(len(grids[f]), cap, max(3, math.ceil(cap / 2))) if adaptive else len(grids[f])
        for f in families
    }
    for i in range(max(coarse.values())):
        for family in families:
            if i < coarse[family]:
                yield family, grids[family][i], "coarse"
    if not adaptive:
        return
    stale = {f: 0 for f in families}
    while True:
        if not config.get("trials_per_family") and sum(counts.values()) >= cap:
            return
        progressed = False
        for family in families:
            if family == "naive" or counts[family] >= cap or stale[family] >= 3:
                continue
            history = histories.get(family, [])
            proposal = refine_parameters(family, history, config)
            stage = "refinement"
            if proposal is None:
                seen = {fingerprint(t["parameters"]) for t in history}
                proposal = next((p for p in grids[family] if fingerprint(p) not in seen), None)
                stage = "exploration"
            if proposal is None:
                continue
            previous = bests.get(family, {}).get("score", math.inf)
            yield family, proposal, stage
            current = bests.get(family, {}).get("score", math.inf)
            stale[family] = 0 if current < previous * 0.999 else stale[family] + 1
            progressed = True
        if not progressed:
            break


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
