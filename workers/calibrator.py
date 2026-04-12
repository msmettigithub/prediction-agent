import numpy as np
from typing import Union, List
import logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "blend_weight": 0.90,
    "stretch_k": 1.5,
    "prob_floor": 0.05,
    "prob_ceiling": 0.95,
    "use_geometric_mean": True,
}


def get_config_value(config: dict, key: str):
    if config is None:
        return DEFAULT_CONFIG[key]
    return config.get(key, DEFAULT_CONFIG[key])


def stretch(p: float, k: float = 1.5) -> float:
    pk = p ** k
    one_minus_pk = (1.0 - p) ** k
    denom = pk + one_minus_pk
    if denom == 0:
        return p
    return pk / denom


def apply_floor_ceiling(p: float, floor: float = 0.05, ceiling: float = 0.95) -> float:
    return max(floor, min(ceiling, p))


def blend_with_prior(raw: float, weight: float, prior: float = 0.5) -> float:
    return weight * raw + (1.0 - weight) * prior


def geometric_mean(probabilities: List[float]) -> float:
    if not probabilities:
        raise ValueError("Cannot compute geometric mean of empty list")
    log_sum = sum(np.log(p) for p in probabilities)
    return np.exp(log_sum / len(probabilities))


def arithmetic_mean(probabilities: List[float]) -> float:
    if not probabilities:
        raise ValueError("Cannot compute arithmetic mean of empty list")
    return sum(probabilities) / len(probabilities)


def ensemble_average(probabilities: List[float], use_geometric: bool = True) -> float:
    if len(probabilities) == 1:
        return probabilities[0]
    if use_geometric:
        return geometric_mean(probabilities)
    return arithmetic_mean(probabilities)


def calibrate(
    raw_prob: Union[float, List[float]],
    config: dict = None,
) -> float:
    blend_weight = get_config_value(config, "blend_weight")
    stretch_k = get_config_value(config, "stretch_k")
    prob_floor = get_config_value(config, "prob_floor")
    prob_ceiling = get_config_value(config, "prob_ceiling")
    use_geometric_mean = get_config_value(config, "use_geometric_mean")

    if isinstance(raw_prob, (list, tuple, np.ndarray)):
        probs = [float(p) for p in raw_prob]
        probs = [apply_floor_ceiling(p, prob_floor, prob_ceiling) for p in probs]
        p = ensemble_average(probs, use_geometric=use_geometric_mean)
    else:
        p = float(raw_prob)

    p = apply_floor_ceiling(p, prob_floor, prob_ceiling)

    p_blended = blend_with_prior(p, weight=blend_weight, prior=0.5)

    p_stretched = stretch(p_blended, k=stretch_k)

    p_final = apply_floor_ceiling(p_stretched, prob_floor, prob_ceiling)

    logger.debug(
        "calibrate: raw=%.4f blended=%.4f stretched=%.4f final=%.4f "
        "(blend_weight=%.2f, k=%.2f)",
        p, p_blended, p_stretched, p_final, blend_weight, stretch_k,
    )

    return p_final


def calibrate_batch(
    raw_probs: List[float],
    config: dict = None,
) -> List[float]:
    return [calibrate(p, config=config) for p in raw_probs]


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    test_cases = [0.38, 0.50, 0.62, 0.70, 0.30]
    print("Single probability calibration:")
    for raw in test_cases:
        cal = calibrate(raw)
        print(f"  raw={raw:.2f} -> calibrated={cal:.4f}")

    print("\nEnsemble calibration (geometric mean):")
    ensemble_input = [0.60, 0.64, 0.58]
    cal_ensemble = calibrate(ensemble_input)
    print(f"  inputs={ensemble_input} -> calibrated={cal_ensemble:.4f}")

    print("\nEnsemble calibration (arithmetic mean):")
    cal_arith = calibrate(ensemble_input, config={"use_geometric_mean": False})
    print(f"  inputs={ensemble_input} -> calibrated={cal_arith:.4f}")