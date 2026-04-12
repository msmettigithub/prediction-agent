import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

EXTREMIZE_K = 1.6
CONFIDENCE_FLOOR = 0.03
CLIP_LOW = 0.03
CLIP_HIGH = 0.97


def extremize(p: float, k: float = EXTREMIZE_K) -> float:
    p = max(1e-9, min(1 - 1e-9, float(p)))
    logit = math.log(p / (1.0 - p))
    stretched = k * logit
    result = 1.0 / (1.0 + math.exp(-stretched))
    return max(CLIP_LOW, min(CLIP_HIGH, result))


def apply_extremize_if_confident(p: float, k: float = EXTREMIZE_K, floor: float = CONFIDENCE_FLOOR) -> float:
    if abs(p - 0.5) > floor:
        p_new = extremize(p, k)
        logger.debug(
            "extremize: before=%.4f after=%.4f delta=%.4f (k=%.2f)",
            p, p_new, p_new - p, k,
        )
        return p_new
    logger.debug("extremize: skipped p=%.4f (|p-0.5|=%.4f <= floor=%.4f)", p, abs(p - 0.5), floor)
    return p


class Calibrator:
    def __init__(
        self,
        method: str = "platt",
        extremize_k: float = EXTREMIZE_K,
        confidence_floor: float = CONFIDENCE_FLOOR,
    ):
        self.method = method
        self.extremize_k = extremize_k
        self.confidence_floor = confidence_floor
        self._platt_a: float = 1.0
        self._platt_b: float = 0.0
        self._isotonic = None
        self._fitted = False

    def fit(self, raw_probs, labels):
        import numpy as np

        raw_probs = np.asarray(raw_probs, dtype=float)
        labels = np.asarray(labels, dtype=float)

        if self.method == "platt":
            from scipy.special import expit, logit as sp_logit
            from scipy.optimize import minimize

            def neg_log_loss(params):
                a, b = params
                scores = sp_logit(np.clip(raw_probs, 1e-9, 1 - 1e-9))
                p = expit(a * scores + b)
                p = np.clip(p, 1e-9, 1 - 1e-9)
                return -np.mean(labels * np.log(p) + (1 - labels) * np.log(1 - p))

            result = minimize(neg_log_loss, [1.0, 0.0], method="Nelder-Mead")
            self._platt_a, self._platt_b = result.x

        elif self.method == "isotonic":
            from sklearn.isotonic import IsotonicRegression

            self._isotonic = IsotonicRegression(out_of_bounds="clip")
            self._isotonic.fit(raw_probs, labels)

        self._fitted = True
        logger.info("Calibrator fitted: method=%s", self.method)

    def calibrate(self, p: float) -> float:
        raw_before = float(p)

        if self._fitted:
            if self.method == "platt":
                from scipy.special import expit, logit as sp_logit

                score = sp_logit(max(1e-9, min(1 - 1e-9, raw_before)))
                p = float(expit(self._platt_a * score + self._platt_b))

            elif self.method == "isotonic" and self._isotonic is not None:
                import numpy as np

                p = float(self._isotonic.predict([raw_before])[0])

        mid_p = float(p)

        final_p = apply_extremize_if_confident(mid_p, k=self.extremize_k, floor=self.confidence_floor)

        logger.info(
            "calibrate: raw_input=%.4f post_scaling=%.4f post_extremize=%.4f",
            raw_before,
            mid_p,
            final_p,
        )

        return final_p

    def calibrate_batch(self, probs):
        return [self.calibrate(p) for p in probs]


_default_calibrator: Optional[Calibrator] = None


def get_default_calibrator() -> Calibrator:
    global _default_calibrator
    if _default_calibrator is None:
        _default_calibrator = Calibrator()
    return _default_calibrator


def calibrate_probability(p: float, calibrator: Optional[Calibrator] = None) -> float:
    if calibrator is None:
        calibrator = get_default_calibrator()
    return calibrator.calibrate(p)