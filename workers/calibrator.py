import logging
import numpy as np
from config import CALIBRATION_ALPHA, CALIBRATION_MIN_DISTANCE, RL_DEPLOY_RATE

logger = logging.getLogger(__name__)


def extremize(p: float, alpha: float = None) -> float:
    if alpha is None:
        alpha = CALIBRATION_ALPHA
    p = float(np.clip(p, 1e-9, 1 - 1e-9))
    p_alpha = p ** alpha
    one_minus_alpha = (1.0 - p) ** alpha
    return p_alpha / (p_alpha + one_minus_alpha)


def calibrate(p_raw: float, alpha: float = None, min_distance: float = None) -> dict:
    if alpha is None:
        alpha = CALIBRATION_ALPHA
    if min_distance is None:
        min_distance = CALIBRATION_MIN_DISTANCE

    p_raw = float(np.clip(p_raw, 1e-9, 1 - 1e-9))

    distance_from_center = abs(p_raw - 0.5)
    if distance_from_center < min_distance:
        logger.info(
            f"[calibrator] SKIP: raw={p_raw:.4f} |p-0.5|={distance_from_center:.4f} < threshold={min_distance:.4f}"
        )
        return {
            "skip": True,
            "reason": "too_uncertain",
            "p_raw": p_raw,
            "p_final": None,
            "distance_from_center": distance_from_center,
        }

    p_ext = extremize(p_raw, alpha=alpha)

    p_final = float(np.clip(p_ext, 0.05, 0.95))

    logger.info(
        f"[calibrator] raw={p_raw:.4f} extremized={p_ext:.4f} final={p_final:.4f} "
        f"alpha={alpha:.3f} |p-0.5|={distance_from_center:.4f}"
    )

    return {
        "skip": False,
        "p_raw": p_raw,
        "p_extremized": p_ext,
        "p_final": p_final,
        "alpha": alpha,
        "distance_from_center": distance_from_center,
        "rl_tracking": {
            "before": p_raw,
            "after": p_final,
            "delta": p_final - p_raw,
            "alpha_used": alpha,
        },
    }


def should_deploy(deploy_rate: float = None) -> bool:
    if deploy_rate is None:
        deploy_rate = RL_DEPLOY_RATE
    return float(np.random.random()) < deploy_rate


def run_calibration_pipeline(p_raw: float) -> dict:
    result = calibrate(p_raw)

    if result["skip"]:
        return result

    result["deploy"] = should_deploy()

    logger.info(
        f"[calibrator] pipeline complete: p_raw={p_raw:.4f} "
        f"p_final={result['p_final']:.4f} deploy={result['deploy']}"
    )

    return result