import numpy as np
from scipy.special import expit, logit
import logging

logger = logging.getLogger(__name__)

SHRINKAGE_BASE = 0.12
LOGISTIC_STRETCH_K = 1.4
FLOOR = 0.08
CEILING = 0.92
SEPARATION_THRESHOLD = 0.10


def _logistic_stretch(p: float, k: float = LOGISTIC_STRETCH_K) -> float:
    p_clipped = np.clip(p, 1e-6, 1 - 1e-6)
    logit_p = logit(p_clipped)
    logit_half = logit(0.5)
    stretched = expit(k * (logit_p - logit_half))
    return float(np.clip(stretched, FLOOR, CEILING))


def _count_corroborating_signals(signals: dict) -> int:
    count = 0
    base_rate = signals.get("base_rate")
    trend = signals.get("trend")
    sentiment = signals.get("sentiment")

    direction = None
    if base_rate is not None:
        if base_rate > 0.55:
            direction = "up"
            count += 1
        elif base_rate < 0.45:
            direction = "down"
            count += 1

    if trend is not None and direction is not None:
        if direction == "up" and trend > 0.55:
            count += 1
        elif direction == "down" and trend < 0.45:
            count += 1

    if sentiment is not None and direction is not None:
        if direction == "up" and sentiment > 0.55:
            count += 1
        elif direction == "down" and sentiment < 0.45:
            count += 1

    return max(0, count - 1)


def calibrate(raw_prob: float, signals: dict = None) -> float:
    if signals is None:
        signals = {}

    raw_prob = float(np.clip(raw_prob, 1e-6, 1 - 1e-6))

    corroborating = _count_corroborating_signals(signals)
    shrinkage = SHRINKAGE_BASE * (0.5 ** corroborating)
    shrinkage = max(shrinkage, 0.02)

    shrunk = shrinkage * 0.5 + (1.0 - shrinkage) * raw_prob

    stretched = _logistic_stretch(shrunk, k=LOGISTIC_STRETCH_K)

    result = float(np.clip(stretched, FLOOR, CEILING))

    logger.debug(
        "calibrate: raw=%.4f corroborating=%d shrinkage=%.4f "
        "shrunk=%.4f stretched=%.4f result=%.4f",
        raw_prob, corroborating, shrinkage, shrunk, stretched, result,
    )

    return result


def calibrate_batch(raw_probs: np.ndarray, signals_list: list = None) -> np.ndarray:
    if signals_list is None:
        signals_list = [{} for _ in raw_probs]

    results = np.array([
        calibrate(p, s) for p, s in zip(raw_probs, signals_list)
    ])
    return results


def compute_separation(calibrated_probs: np.ndarray, outcomes: np.ndarray) -> float:
    calibrated_probs = np.asarray(calibrated_probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)

    if len(calibrated_probs) == 0 or len(outcomes) == 0:
        return 0.0

    win_mask = outcomes == 1
    loss_mask = outcomes == 0

    if win_mask.sum() == 0 or loss_mask.sum() == 0:
        return 0.0

    mean_win_prob = calibrated_probs[win_mask].mean()
    mean_loss_prob = calibrated_probs[loss_mask].mean()
    separation = float(mean_win_prob - mean_loss_prob)

    logger.info(
        "separation: %.4f (win_mean=%.4f, loss_mean=%.4f, n_wins=%d, n_losses=%d)",
        separation, mean_win_prob, mean_loss_prob, win_mask.sum(), loss_mask.sum(),
    )

    return separation


def rl_deployment_gate(
    calibrated_probs: np.ndarray,
    outcomes: np.ndarray,
    min_separation: float = SEPARATION_THRESHOLD,
) -> bool:
    if len(calibrated_probs) < 10:
        logger.warning(
            "rl_deployment_gate: insufficient samples (%d), requiring at least 10",
            len(calibrated_probs),
        )
        return False

    separation = compute_separation(calibrated_probs, outcomes)
    passes = separation >= min_separation

    logger.info(
        "rl_deployment_gate: separation=%.4f threshold=%.4f passes=%s",
        separation, min_separation, passes,
    )

    return passes


def apply_floor_ceiling(probs: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(probs, dtype=float), FLOOR, CEILING)


def calibration_summary(
    raw_probs: np.ndarray,
    calibrated_probs: np.ndarray,
    outcomes: np.ndarray,
) -> dict:
    raw_probs = np.asarray(raw_probs, dtype=float)
    calibrated_probs = np.asarray(calibrated_probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)

    separation = compute_separation(calibrated_probs, outcomes)
    mean_deviation_raw = float(np.mean(np.abs(raw_probs - 0.5)))
    mean_deviation_cal = float(np.mean(np.abs(calibrated_probs - 0.5)))

    brier = float(np.mean((calibrated_probs - outcomes) ** 2)) if len(outcomes) > 0 else None

    summary = {
        "separation": separation,
        "passes_gate": separation >= SEPARATION_THRESHOLD,
        "mean_deviation_raw": mean_deviation_raw,
        "mean_deviation_calibrated": mean_deviation_cal,
        "amplification_ratio": (
            mean_deviation_cal / mean_deviation_raw
            if mean_deviation_raw > 0 else None
        ),
        "brier_score": brier,
        "n_samples": len(calibrated_probs),
        "shrinkage_base": SHRINKAGE_BASE,
        "logistic_stretch_k": LOGISTIC_STRETCH_K,
        "floor": FLOOR,
        "ceiling": CEILING,
    }

    logger.info("calibration_summary: %s", summary)
    return summary