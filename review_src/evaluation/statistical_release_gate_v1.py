from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from statistics import NormalDist
from typing import Any, Iterable, Mapping, Sequence

from analysis.target_label_contract_v1 import TARGET_CLASSES


STATISTICAL_RELEASE_GATE_VERSION = "StatisticalReleaseGateV1"
BOOTSTRAP_ITERATIONS = 10_000
BOOTSTRAP_SEED = 20260901
BOOTSTRAP_BLOCK_DAYS = 5
ALPHA = 0.05
EPSILON = 1e-6
ECE_BINS = 10

VALID_RESULTS = {
    "pass_for_canary",
    "remain_shadow_noninferior_but_not_superior",
    "remain_shadow_insufficient_power",
    "reject_safety_regression",
    "reject_statistical_regression",
    "invalid_evidence",
}

GATE_SPEC: dict[str, Any] = {
    "version": STATISTICAL_RELEASE_GATE_VERSION,
    "bootstrap": {
        "iterations": BOOTSTRAP_ITERATIONS,
        "seed": BOOTSTRAP_SEED,
        "normal": "five_trading_day_moving_blocks_all_stocks",
        "material_event": "worse_of_trading_day_blocks_and_independent_event_clusters",
    },
    "minimums": {
        "normal": {
            "paired_observations": 2_000,
            "trading_days": 120,
            "stocks": 30,
            "per_class": 100,
            "power": 0.80,
        },
        "material_event": {
            "paired_observations": 300,
            "event_clusters": 50,
            "trading_days": 60,
            "stocks": 20,
            "event_types": 3,
            "clusters_per_event_type": 10,
            "clusters_per_class": 20,
            "large_safety_clusters": 30,
            "power": 0.80,
        },
    },
    "metrics": {
        "primary": "multiclass_brier_sum",
        "secondary": ["clipped_log_loss", "classwise_adaptive_ece", "balanced_accuracy"],
        "safety": [
            "high_confidence_wrong_incidence",
            "high_confidence_correct_coverage",
            "large_or_material_direction_miss_rate",
            "prediction_completion",
        ],
    },
    "thresholds": {
        "high_confidence": 0.70,
        "material_direction_miss_actual_probability": 0.20,
    },
    "noninferiority": {
        "brier": "min(0.002,stable*0.01)",
        "log_loss": "min(0.005,stable*0.01)",
        "ece": 0.005,
        "balanced_accuracy": -0.005,
        "high_confidence_wrong": 0.005,
        "high_confidence_correct": -0.010,
        "safety_miss": 0.010,
        "completion": 0.0,
    },
    "superiority": {
        "target_brier": "max(0.002,stable*0.02)",
        "macro_brier": "max(0.002,stable*0.02)",
        "minimum_target_wins": 2,
    },
    "multiplicity": "hierarchical_holm_bonferroni_fwer_0.05",
    "synthetic_rows_allowed": False,
}
GATE_SPEC_HASH = hashlib.sha256(
    json.dumps(GATE_SPEC, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


LOWER_IS_BETTER = {
    "brier",
    "log_loss",
    "ece",
    "high_confidence_wrong",
    "safety_miss",
}
SAFETY_METRICS = {
    "high_confidence_wrong",
    "high_confidence_correct",
    "safety_miss",
    "completion",
}
ALL_METRICS = (
    "brier",
    "log_loss",
    "ece",
    "balanced_accuracy",
    "high_confidence_wrong",
    "high_confidence_correct",
    "safety_miss",
    "completion",
)


class ReleaseEvidenceError(ValueError):
    pass


def _prediction(row: Mapping[str, Any], role: str, classes: Sequence[str]) -> tuple[list[float], bool]:
    raw = row.get(role)
    prediction = raw if isinstance(raw, Mapping) else {}
    completed = (
        prediction.get("eligible") is True
        and str(prediction.get("completion_state") or "") == "completed"
    )
    probabilities = prediction.get("probabilities")
    if not completed:
        return [1.0 / len(classes)] * len(classes), False
    if not isinstance(probabilities, Mapping) or set(probabilities) != set(classes):
        raise ReleaseEvidenceError(f"{role}_completed_prediction_has_invalid_classes")
    values: list[float] = []
    for label in classes:
        value = probabilities.get(label)
        if isinstance(value, bool):
            raise ReleaseEvidenceError(f"{role}_completed_prediction_has_invalid_probability")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ReleaseEvidenceError(f"{role}_completed_prediction_has_invalid_probability") from exc
        if not math.isfinite(number) or number < 0 or number > 1:
            raise ReleaseEvidenceError(f"{role}_completed_prediction_has_invalid_probability")
        values.append(number)
    if abs(sum(values) - 1.0) > 1e-6:
        raise ReleaseEvidenceError(f"{role}_completed_prediction_probabilities_do_not_sum_to_one")
    return values, True


def _prepared_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    for source in rows:
        row = dict(source)
        target = str(row.get("target_key") or "")
        regime = str(row.get("regime") or "")
        sample_id = str(row.get("sample_id") or "")
        actual = str(row.get("actual_label") or "")
        classes = TARGET_CLASSES.get(target)
        if row.get("synthetic") is True:
            raise ReleaseEvidenceError("synthetic_rows_are_forbidden")
        if not sample_id or classes is None or regime not in {"normal", "material_event"}:
            raise ReleaseEvidenceError("paired_identity_is_invalid")
        identity = (regime, target, sample_id)
        if identity in identities:
            raise ReleaseEvidenceError("paired_identity_is_duplicated")
        identities.add(identity)
        if actual not in classes:
            raise ReleaseEvidenceError("actual_label_is_invalid_or_unavailable")
        if not str(row.get("outcome_revision") or ""):
            raise ReleaseEvidenceError("outcome_revision_is_missing")
        if not str(row.get("prediction_trade_date") or ""):
            raise ReleaseEvidenceError("prediction_trade_date_is_missing")
        if not str(row.get("stock_code") or ""):
            raise ReleaseEvidenceError("stock_code_is_missing")
        stable_probabilities, stable_completed = _prediction(row, "stable", classes)
        candidate_probabilities, candidate_completed = _prediction(row, "candidate", classes)
        prepared.append(
            {
                **row,
                "classes": tuple(classes),
                "actual_index": classes.index(actual),
                "stable_probabilities": stable_probabilities,
                "candidate_probabilities": candidate_probabilities,
                "stable_completed": stable_completed,
                "candidate_completed": candidate_completed,
            }
        )
    return prepared


def _adaptive_ece(probabilities: list[list[float]], actuals: list[int], class_count: int) -> float:
    if not probabilities:
        return math.nan
    sample_count = len(probabilities)
    class_values: list[float] = []
    for class_index in range(class_count):
        order = sorted(range(sample_count), key=lambda index: probabilities[index][class_index])
        bins = [
            order[(index * sample_count) // ECE_BINS : ((index + 1) * sample_count) // ECE_BINS]
            for index in range(ECE_BINS)
        ]
        error = 0.0
        for indices in bins:
            if not indices:
                continue
            confidence = sum(probabilities[index][class_index] for index in indices) / len(indices)
            frequency = sum(actuals[index] == class_index for index in indices) / len(indices)
            error += len(indices) / sample_count * abs(confidence - frequency)
        class_values.append(error)
    return sum(class_values) / len(class_values)


def _metric_bundle(rows: Sequence[Mapping[str, Any]], role: str) -> dict[str, float]:
    if not rows:
        return {metric: math.nan for metric in ALL_METRICS}
    probabilities = [list(row[f"{role}_probabilities"]) for row in rows]
    actuals = [int(row["actual_index"]) for row in rows]
    completed = [bool(row[f"{role}_completed"]) for row in rows]
    class_count = len(rows[0]["classes"])
    brier_values: list[float] = []
    log_values: list[float] = []
    predicted: list[int] = []
    high_wrong = 0
    high_correct = 0
    safety_denominator = 0
    safety_misses = 0
    for row, probs, actual, is_completed in zip(rows, probabilities, actuals, completed):
        brier_values.append(
            sum((probability - (1.0 if index == actual else 0.0)) ** 2 for index, probability in enumerate(probs))
        )
        log_values.append(-math.log(max(EPSILON, min(1.0 - EPSILON, probs[actual]))))
        prediction = max(range(class_count), key=lambda index: probs[index])
        predicted.append(prediction)
        if is_completed and max(probs) >= 0.70:
            if prediction == actual:
                high_correct += 1
            else:
                high_wrong += 1
        neutral_label = "neutral" if "neutral" in row["classes"] else "flat"
        safety_slice = bool(row.get("large_safety_slice")) or str(row.get("regime")) == "material_event"
        if safety_slice and str(row.get("actual_label")) != neutral_label:
            safety_denominator += 1
            if not is_completed or probs[actual] < 0.20:
                safety_misses += 1
    recalls: list[float] = []
    for class_index in range(class_count):
        indices = [index for index, actual in enumerate(actuals) if actual == class_index]
        if indices:
            recalls.append(sum(predicted[index] == class_index for index in indices) / len(indices))
    count = len(rows)
    return {
        "brier": sum(brier_values) / count,
        "log_loss": sum(log_values) / count,
        "ece": _adaptive_ece(probabilities, actuals, class_count),
        "balanced_accuracy": sum(recalls) / len(recalls),
        "high_confidence_wrong": high_wrong / count,
        "high_confidence_correct": high_correct / count,
        "safety_miss": safety_misses / safety_denominator if safety_denominator else 0.0,
        "completion": sum(completed) / count,
    }


def _group_resample_indices(
    rows: Sequence[Mapping[str, Any]],
    *,
    rng: random.Random,
    mode: str,
) -> list[int]:
    if mode == "event_cluster":
        grouped: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            cluster = str(row.get("event_cluster_id") or "")
            if not cluster:
                raise ReleaseEvidenceError("material_event_cluster_id_is_missing")
            grouped[cluster].append(index)
        units = list(grouped.values())
        return [index for _ in units for index in rng.choice(units)]

    by_date: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_date[str(row.get("prediction_trade_date"))].append(index)
    dates = sorted(by_date)
    if not dates:
        return []
    blocks = [
        [dates[(start + offset) % len(dates)] for offset in range(min(BOOTSTRAP_BLOCK_DAYS, len(dates)))]
        for start in range(len(dates))
    ]
    selected_dates: list[str] = []
    while len(selected_dates) < len(dates):
        selected_dates.extend(rng.choice(blocks))
    selected_dates = selected_dates[: len(dates)]
    return [index for selected_date in selected_dates for index in by_date[selected_date]]


def _bootstrap_differences(
    rows: Sequence[Mapping[str, Any]],
    *,
    iterations: int,
    seed: int,
    mode: str,
) -> dict[str, list[float]]:
    rng = random.Random(seed)
    results = {metric: [] for metric in ALL_METRICS}
    for _ in range(iterations):
        indices = _group_resample_indices(rows, rng=rng, mode=mode)
        sample = [rows[index] for index in indices]
        stable = _metric_bundle(sample, "stable")
        candidate = _metric_bundle(sample, "candidate")
        for metric in ALL_METRICS:
            results[metric].append(candidate[metric] - stable[metric])
    return results


def _one_sided_p_value(observed: float, samples: Sequence[float], *, boundary: float, lower_better: bool) -> float:
    if len(samples) < 2:
        return 1.0
    standard_error = statistics.stdev(samples)
    if standard_error <= 0:
        passes = observed <= boundary if lower_better else observed >= boundary
        return 0.0 if passes else 1.0
    z_score = ((boundary - observed) if lower_better else (observed - boundary)) / standard_error
    return max(0.0, min(1.0, 1.0 - NormalDist().cdf(z_score)))


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return math.nan
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def _bootstrap_macro_brier(
    rows: Sequence[Mapping[str, Any]],
    *,
    iterations: int,
    seed: int,
    mode: str,
) -> list[float]:
    rng = random.Random(seed)
    differences: list[float] = []
    for _ in range(iterations):
        indices = _group_resample_indices(rows, rng=rng, mode=mode)
        sample = [rows[index] for index in indices]
        target_differences: list[float] = []
        for target in TARGET_CLASSES:
            target_rows = [row for row in sample if str(row.get("target_key")) == target]
            if not target_rows:
                continue
            stable = _metric_bundle(target_rows, "stable")["brier"]
            candidate = _metric_bundle(target_rows, "candidate")["brier"]
            target_differences.append(candidate - stable)
        differences.append(
            sum(target_differences) / len(target_differences) if target_differences else 0.0
        )
    return differences


def _holm_rejections(p_values: Mapping[str, float], *, alpha: float = ALPHA) -> set[str]:
    ordered = sorted((float(value), key) for key, value in p_values.items())
    rejected: set[str] = set()
    total = len(ordered)
    for index, (p_value, key) in enumerate(ordered):
        if p_value <= alpha / (total - index):
            rejected.add(key)
        else:
            break
    return rejected


def _minimum_practical_effect(stable_brier: float) -> float:
    return max(0.002, stable_brier * 0.02)


def _paired_power(rows: Sequence[Mapping[str, Any]], regime: str) -> float:
    grouped: dict[str, list[float]] = defaultdict(list)
    unit_key = "prediction_trade_date" if regime == "normal" else "event_cluster_id"
    for row in rows:
        stable = _metric_bundle([row], "stable")["brier"]
        candidate = _metric_bundle([row], "candidate")["brier"]
        grouped[str(row.get(unit_key) or "")].append(stable - candidate)
    unit_means = [sum(values) / len(values) for values in grouped.values() if values]
    if len(unit_means) < 2:
        return 0.0
    standard_deviation = statistics.stdev(unit_means)
    effect = _minimum_practical_effect(_metric_bundle(rows, "stable")["brier"])
    if standard_deviation <= 0:
        return 1.0
    standard_error = standard_deviation / math.sqrt(len(unit_means))
    return max(0.0, min(1.0, NormalDist().cdf(effect / standard_error - 1.6448536269514722)))


def _coverage(rows: Sequence[Mapping[str, Any]], regime: str) -> dict[str, Any]:
    labels = Counter(str(row.get("actual_label")) for row in rows)
    result: dict[str, Any] = {
        "paired_observations": len(rows),
        "trading_days": len({str(row.get("prediction_trade_date")) for row in rows}),
        "stocks": len({str(row.get("stock_code")) for row in rows}),
        "class_counts": dict(sorted(labels.items())),
        "power": round(_paired_power(rows, regime), 6) if rows else 0.0,
    }
    if regime == "material_event":
        clusters = {str(row.get("event_cluster_id") or "") for row in rows if str(row.get("event_cluster_id") or "")}
        by_type: dict[str, set[str]] = defaultdict(set)
        by_class: dict[str, set[str]] = defaultdict(set)
        large_clusters: set[str] = set()
        for row in rows:
            cluster = str(row.get("event_cluster_id") or "")
            if cluster:
                by_type[str(row.get("event_type") or "")].add(cluster)
                by_class[str(row.get("actual_label") or "")].add(cluster)
                if row.get("large_safety_slice") is True:
                    large_clusters.add(cluster)
        result.update(
            {
                "event_clusters": len(clusters),
                "event_types": len([key for key in by_type if key]),
                "clusters_per_event_type": {key: len(value) for key, value in sorted(by_type.items()) if key},
                "clusters_per_class": {key: len(value) for key, value in sorted(by_class.items())},
                "large_safety_clusters": len(large_clusters),
            }
        )
    return result


def _coverage_ready(coverage: Mapping[str, Any], regime: str, classes: Sequence[str]) -> tuple[bool, list[str]]:
    minimum = GATE_SPEC["minimums"][regime]
    reasons: list[str] = []
    for key in ("paired_observations", "trading_days", "stocks"):
        if int(coverage.get(key) or 0) < int(minimum[key]):
            reasons.append(f"{key}_below_minimum")
    if regime == "normal":
        counts = coverage.get("class_counts") if isinstance(coverage.get("class_counts"), Mapping) else {}
        if any(int(counts.get(label) or 0) < int(minimum["per_class"]) for label in classes):
            reasons.append("per_class_count_below_minimum")
    else:
        for key in ("event_clusters", "event_types", "large_safety_clusters"):
            if int(coverage.get(key) or 0) < int(minimum[key]):
                reasons.append(f"{key}_below_minimum")
        per_type = coverage.get("clusters_per_event_type") if isinstance(coverage.get("clusters_per_event_type"), Mapping) else {}
        if len(per_type) < int(minimum["event_types"]) or any(
            int(value) < int(minimum["clusters_per_event_type"]) for value in per_type.values()
        ):
            reasons.append("clusters_per_event_type_below_minimum")
        per_class = coverage.get("clusters_per_class") if isinstance(coverage.get("clusters_per_class"), Mapping) else {}
        if any(int(per_class.get(label) or 0) < int(minimum["clusters_per_class"]) for label in classes):
            reasons.append("clusters_per_class_below_minimum")
    if float(coverage.get("power") or 0.0) < float(minimum["power"]):
        reasons.append("power_below_minimum")
    return not reasons, reasons


def _ni_margin(metric: str, stable_value: float) -> float:
    if metric == "brier":
        return min(0.002, stable_value * 0.01)
    if metric == "log_loss":
        return min(0.005, stable_value * 0.01)
    return {
        "ece": 0.005,
        "balanced_accuracy": 0.005,
        "high_confidence_wrong": 0.005,
        "high_confidence_correct": 0.010,
        "safety_miss": 0.010,
        "completion": 0.0,
    }[metric]


def _evaluate_target(
    rows: Sequence[Mapping[str, Any]],
    *,
    regime: str,
    target: str,
    iterations: int,
) -> dict[str, Any]:
    stable = _metric_bundle(rows, "stable")
    candidate = _metric_bundle(rows, "candidate")
    day_bootstrap = _bootstrap_differences(
        rows,
        iterations=iterations,
        seed=BOOTSTRAP_SEED + sum(ord(char) for char in f"{regime}:{target}:day"),
        mode="day_block",
    )
    cluster_bootstrap = (
        _bootstrap_differences(
            rows,
            iterations=iterations,
            seed=BOOTSTRAP_SEED + sum(ord(char) for char in f"{regime}:{target}:cluster"),
            mode="event_cluster",
        )
        if regime == "material_event"
        else None
    )
    hypotheses: dict[str, Any] = {}
    for metric in ALL_METRICS:
        observed = candidate[metric] - stable[metric]
        margin = _ni_margin(metric, stable[metric])
        lower_better = metric in LOWER_IS_BETTER
        boundary = margin if lower_better else -margin
        day_p = _one_sided_p_value(observed, day_bootstrap[metric], boundary=boundary, lower_better=lower_better)
        p_value = day_p
        bootstrap_mode = "day_block"
        confidence_bound = _quantile(day_bootstrap[metric], 0.95 if lower_better else 0.05)
        if cluster_bootstrap is not None:
            cluster_p = _one_sided_p_value(
                observed,
                cluster_bootstrap[metric],
                boundary=boundary,
                lower_better=lower_better,
            )
            p_value = max(day_p, cluster_p)
            bootstrap_mode = "worse_of_day_block_and_event_cluster"
            cluster_bound = _quantile(
                cluster_bootstrap[metric],
                0.95 if lower_better else 0.05,
            )
            confidence_bound = (
                max(confidence_bound, cluster_bound)
                if lower_better
                else min(confidence_bound, cluster_bound)
            )
        hypotheses[metric] = {
            "stable": stable[metric],
            "candidate": candidate[metric],
            "candidate_minus_stable": observed,
            "noninferiority_boundary": boundary,
            "p_value": p_value,
            "bootstrap_mode": bootstrap_mode,
            "one_sided_95_confidence_bound": confidence_bound,
            "confidence_bound_side": "upper" if lower_better else "lower",
        }
    superiority_threshold = _minimum_practical_effect(stable["brier"])
    observed_brier = candidate["brier"] - stable["brier"]
    day_superiority_p = _one_sided_p_value(
        observed_brier,
        day_bootstrap["brier"],
        boundary=-superiority_threshold,
        lower_better=True,
    )
    superiority_p = day_superiority_p
    superiority_bound = _quantile(day_bootstrap["brier"], 0.95)
    if cluster_bootstrap is not None:
        superiority_p = max(
            day_superiority_p,
            _one_sided_p_value(
                observed_brier,
                cluster_bootstrap["brier"],
                boundary=-superiority_threshold,
                lower_better=True,
            ),
        )
        superiority_bound = max(
            superiority_bound,
            _quantile(cluster_bootstrap["brier"], 0.95),
        )
    return {
        "target_key": target,
        "stable_metrics": stable,
        "candidate_metrics": candidate,
        "noninferiority_hypotheses": hypotheses,
        "brier_superiority": {
            "minimum_point_improvement": superiority_threshold,
            "point_improvement": stable["brier"] - candidate["brier"],
            "p_value": superiority_p,
            "candidate_minus_stable_upper_95_bound": superiority_bound,
        },
    }


def evaluate_statistical_release_gate(
    rows: Iterable[Mapping[str, Any]],
    *,
    bootstrap_iterations: int = BOOTSTRAP_ITERATIONS,
) -> dict[str, Any]:
    """Evaluate predeclared paired evidence without changing stable production behavior."""

    try:
        prepared = _prepared_rows(rows)
    except ReleaseEvidenceError as exc:
        return {
            "gate_version": STATISTICAL_RELEASE_GATE_VERSION,
            "gate_spec_hash": GATE_SPEC_HASH,
            "result": "invalid_evidence",
            "reason_codes": [str(exc)],
            "regimes": {},
        }
    if bootstrap_iterations != BOOTSTRAP_ITERATIONS:
        return {
            "gate_version": STATISTICAL_RELEASE_GATE_VERSION,
            "gate_spec_hash": GATE_SPEC_HASH,
            "result": "invalid_evidence",
            "reason_codes": ["bootstrap_iterations_are_invalid"],
            "regimes": {},
        }

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in prepared:
        grouped[(str(row["regime"]), str(row["target_key"]))].append(row)

    regimes: dict[str, Any] = {}
    for regime in ("normal", "material_event"):
        coverage_by_target: dict[str, Any] = {}
        coverage_reasons: list[str] = []
        ready = True
        for target, classes in TARGET_CLASSES.items():
            target_rows = grouped.get((regime, target), [])
            coverage = _coverage(target_rows, regime)
            target_ready, reasons = _coverage_ready(coverage, regime, classes)
            coverage_by_target[target] = {**coverage, "ready": target_ready, "reason_codes": reasons}
            if not target_ready:
                ready = False
                coverage_reasons.extend(f"{target}:{reason}" for reason in reasons)
        if not ready:
            regimes[regime] = {
                "result": "remain_shadow_insufficient_power",
                "coverage": coverage_by_target,
                "reason_codes": coverage_reasons,
                "targets": {},
            }
            continue

        targets = {
            target: _evaluate_target(
                grouped[(regime, target)],
                regime=regime,
                target=target,
                iterations=bootstrap_iterations,
            )
            for target in TARGET_CLASSES
        }
        stage1_p_values = {
            f"{target}:{metric}": details["noninferiority_hypotheses"][metric]["p_value"]
            for target, details in targets.items()
            for metric in ALL_METRICS
        }
        stage1_rejected = _holm_rejections(stage1_p_values)
        stage1_failed = set(stage1_p_values) - stage1_rejected
        if stage1_failed:
            safety_failed = any(key.split(":", 1)[1] in SAFETY_METRICS for key in stage1_failed)
            regimes[regime] = {
                "result": "reject_safety_regression" if safety_failed else "reject_statistical_regression",
                "coverage": coverage_by_target,
                "reason_codes": [f"noninferiority_not_proven:{key}" for key in sorted(stage1_failed)],
                "targets": targets,
                "holm_stage1": {"p_values": stage1_p_values, "rejected": sorted(stage1_rejected)},
            }
            continue

        stable_macro = sum(details["stable_metrics"]["brier"] for details in targets.values()) / len(targets)
        candidate_macro = sum(details["candidate_metrics"]["brier"] for details in targets.values()) / len(targets)
        macro_threshold = _minimum_practical_effect(stable_macro)
        regime_rows = [row for target in TARGET_CLASSES for row in grouped[(regime, target)]]
        macro_day_bootstrap = _bootstrap_macro_brier(
            regime_rows,
            iterations=bootstrap_iterations,
            seed=BOOTSTRAP_SEED + sum(ord(char) for char in f"{regime}:macro:day"),
            mode="day_block",
        )
        macro_observed = candidate_macro - stable_macro
        macro_p = _one_sided_p_value(
            macro_observed,
            macro_day_bootstrap,
            boundary=-macro_threshold,
            lower_better=True,
        )
        macro_bound = _quantile(macro_day_bootstrap, 0.95)
        if regime == "material_event":
            macro_cluster_bootstrap = _bootstrap_macro_brier(
                regime_rows,
                iterations=bootstrap_iterations,
                seed=BOOTSTRAP_SEED + sum(ord(char) for char in f"{regime}:macro:cluster"),
                mode="event_cluster",
            )
            macro_p = max(
                macro_p,
                _one_sided_p_value(
                    macro_observed,
                    macro_cluster_bootstrap,
                    boundary=-macro_threshold,
                    lower_better=True,
                ),
            )
            macro_bound = max(macro_bound, _quantile(macro_cluster_bootstrap, 0.95))
        stage2_p_values = {
            "macro_brier": macro_p,
            **{
                f"target_brier:{target}": details["brier_superiority"]["p_value"]
                for target, details in targets.items()
            },
        }
        stage2_rejected = _holm_rejections(stage2_p_values)
        target_wins = [
            target
            for target, details in targets.items()
            if f"target_brier:{target}" in stage2_rejected
            and details["brier_superiority"]["point_improvement"]
            >= details["brier_superiority"]["minimum_point_improvement"]
        ]
        macro_point_pass = stable_macro - candidate_macro >= macro_threshold
        superiority_pass = (
            "macro_brier" in stage2_rejected
            and macro_point_pass
            and len(target_wins) >= int(GATE_SPEC["superiority"]["minimum_target_wins"])
        )
        regimes[regime] = {
            "result": "pass_for_canary" if superiority_pass else "remain_shadow_noninferior_but_not_superior",
            "coverage": coverage_by_target,
            "reason_codes": [] if superiority_pass else ["predeclared_superiority_not_proven"],
            "targets": targets,
            "holm_stage1": {"p_values": stage1_p_values, "rejected": sorted(stage1_rejected)},
            "holm_stage2": {"p_values": stage2_p_values, "rejected": sorted(stage2_rejected)},
            "macro_brier": {
                "stable": stable_macro,
                "candidate": candidate_macro,
                "point_improvement": stable_macro - candidate_macro,
                "minimum_point_improvement": macro_threshold,
                "p_value": macro_p,
                "candidate_minus_stable_upper_95_bound": macro_bound,
            },
            "target_superiority_wins": target_wins,
        }

    regime_results = {details["result"] for details in regimes.values()}
    precedence = (
        "invalid_evidence",
        "reject_safety_regression",
        "reject_statistical_regression",
        "remain_shadow_insufficient_power",
        "remain_shadow_noninferior_but_not_superior",
        "pass_for_canary",
    )
    overall = next((result for result in precedence if result in regime_results), "invalid_evidence")
    return {
        "gate_version": STATISTICAL_RELEASE_GATE_VERSION,
        "gate_spec_hash": GATE_SPEC_HASH,
        "bootstrap_iterations": bootstrap_iterations,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "result": overall,
        "reason_codes": [],
        "regimes": regimes,
    }
