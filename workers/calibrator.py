import numpy as np
from typing import Optional

CONFIDENCE_EXPANSION_ENABLED = True


class Calibrator:
    def __init__(self, method: str = "isotonic"):
        self.method = method
        self.model = None
        self._is_fitted = False

    def fit(self, probs: np.ndarray, labels: np.ndarray) -> None:
        from sklearn.isotonic import IsotonicRegression
        from sklearn.linear_model import LogisticRegression

        if self.method == "isotonic":
            self.model = IsotonicRegression(out_of_bounds="clip")
            self.model.fit(probs, labels)
        elif self.method == "platt":
            self.model = LogisticRegression()
            self.model.fit(probs.reshape(-1, 1), labels)
        else:
            raise ValueError(f"Unknown calibration method: {self.method}")

        self._is_fitted = True

    def predict(self, probs: np.ndarray) -> np.ndarray:
        if not self._is_fitted or self.model is None:
            return probs

        if self.method == "isotonic":
            return self.model.predict(probs)
        elif self.method == "platt":
            return self.model.predict_proba(probs.reshape(-1, 1))[:, 1]
        return probs

    def calibrate(self, raw_prob: float, signals: Optional[dict] = None) -> float:
        p = float(raw_prob)
        p = max(0.0, min(1.0, p))

        if self._is_fitted and self.model is not None:
            arr = np.array([p])
            p = float(self.predict(arr)[0])

        p = max(0.0, min(1.0, p))

        if CONFIDENCE_EXPANSION_ENABLED and signals is not None and len(signals) > 0:
            p = self._apply_confidence_expansion(p, signals)

        return p

    def _apply_confidence_expansion(self, p: float, signals: dict) -> float:
        direction = 1 if p >= 0.5 else -1

        concordant_count = 0
        for key, value in signals.items():
            try:
                signal_val = float(value)
            except (TypeError, ValueError):
                continue

            if direction == 1 and signal_val >= 0.5:
                concordant_count += 1
            elif direction == -1 and signal_val < 0.5:
                concordant_count += 1

        if concordant_count >= 3:
            stretch_factor = 1.0 + 0.1 * min(concordant_count, 5)
            adjusted_p = 0.5 + (p - 0.5) * stretch_factor
            adjusted_p = max(0.05, min(0.95, adjusted_p))
            return adjusted_p

        return p

    def is_fitted(self) -> bool:
        return self._is_fitted

    def reset(self) -> None:
        self.model = None
        self._is_fitted = False