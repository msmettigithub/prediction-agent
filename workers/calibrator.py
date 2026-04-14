import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

SHARPEN_ENABLED = True

_SHARPEN_K = {
    3: 1.3,
    4: 1.6,
}


def sharpen_probability(
    p: float,
    signal_directions: list[Optional[bool]],
    label: str = "",
) -> float:
    """
    Conditionally sharpen a probability based on cross-signal agreement.

    Parameters
    ----------
    p:
        Calibrated probability in [0, 1].
    signal_directions:
        Each element is True (signal favours p>0.5), False (signal favours
        p<0.5), or None (signal unavailable / inconclusive).
    label:
        Arbitrary identifier used in log output for RL tracking.

    Returns
    -------
    Sharpened probability clamped to [0.05, 0.95].
    """
    if not SHARPEN_ENABLED:
        return p

    p_original = float(p)
    p = max(0.0, min(1.0, p_original))

    centre_direction: Optional[bool] = None
    if p > 0.5:
        centre_direction = True
    elif p < 0.5:
        centre_direction = False
    else:
        logger.debug("sharpen_probability[%s]: p=0.5, no sharpening applied", label)
        return p

    available = [d for d in signal_directions if d is not None]
    if not available:
        logger.debug(
            "sharpen_probability[%s]: no available signals, skipping sharpening",
            label,
        )
        return p

    agreeing = sum(1 for d in available if d == centre_direction)

    if agreeing < 3:
        logger.debug(
            "sharpen_probability[%s]: agreement=%d < 3, skipping sharpening "
            "(p=%.4f)",
            label,
            agreeing,
            p,
        )
        return p

    k = _SHARPEN_K.get(min(agreeing, 4), _SHARPEN_K[4])

    sign = 1.0 if p > 0.5 else -1.0
    deviation = abs(2.0 * (p - 0.5))

    try:
        sharpened_deviation = math.pow(deviation, 1.0 / k)
    except (ValueError, ZeroDivisionError):
        logger.warning(
            "sharpen_probability[%s]: math error during sharpening, "
            "returning original p=%.4f",
            label,
            p,
        )
        return max(0.05, min(0.95, p))

    p_sharp = 0.5 + sign * sharpened_deviation * 0.5
    p_sharp = max(0.05, min(0.95, p_sharp))

    logger.info(
        "sharpen_probability[%s]: p_before=%.4f p_after=%.4f "
        "agreement=%d/%d k=%.1f label=%s",
        label,
        p,
        p_sharp,
        agreeing,
        len(available),
        k,
        label,
    )

    return p_sharp


def extremize(p: float, alpha: float = 1.3) -> float:
    """
    Log-odds extremizing transform. Safe for all p in [0, 1].
    Fixed point at p=0.5. Alpha>1 sharpens, alpha=1 is identity.
    """
    p = float(max(0.02, min(0.98, p)))

    log_odds = math.log(p / (1.0 - p))
    scaled_log_odds = alpha * log_odds

    try:
        p_sharp = 1.0 / (1.0 + math.exp(-scaled_log_odds))
    except OverflowError:
        p_sharp = 0.02 if scaled_log_odds < 0 else 0.98

    return float(max(0.02, min(0.98, p_sharp)))


class Calibrator:
    """
    Probability calibrator that applies log-odds extremizing followed by
    optional cross-signal agreement sharpening.
    """

    def __init__(
        self,
        alpha: float = 1.3,
        sharpen_enabled: Optional[bool] = None,
    ) -> None:
        self.alpha = alpha
        self._sharpen_enabled = (
            SHARPEN_ENABLED if sharpen_enabled is None else sharpen_enabled
        )

    def calibrate(
        self,
        p: float,
        signal_directions: Optional[list[Optional[bool]]] = None,
        label: str = "",
    ) -> float:
        """
        Full calibration pipeline:
          1. Clip input to a valid range.
          2. Apply log-odds extremizing (alpha-scaling).
          3. Apply cross-signal sharpening when signals agree.
          4. Return clamped probability.

        Parameters
        ----------
        p:
            Raw model probability.
        signal_directions:
            Optional list of per-signal direction votes (True / False / None).
            When omitted or empty the sharpening step is skipped.
        label:
            Identifier for log/RL tracking.

        Returns
        -------
        Calibrated probability in [0.05, 0.95].
        """
        try:
            p_extremized = extremize(float(p), self.alpha)
        except Exception as exc:
            logger.warning(
                "calibrate[%s]: extremize failed (%s), using clipped raw p",
                label,
                exc,
            )
            p_extremized = float(max(0.05, min(0.95, p)))

        if signal_directions and self._sharpen_enabled:
            p_final = sharpen_probability(
                p_extremized,
                signal_directions=signal_directions,
                label=label,
            )
        else:
            p_final = max(0.05, min(0.95, p_extremized))

        logger.debug(
            "calibrate[%s]: p_raw=%.4f p_extremized=%.4f p_final=%.4f",
            label,
            p,
            p_extremized,
            p_final,
        )

        return p_final

    def calibrate_batch(
        self,
        probabilities: list[float],
        signal_directions_batch: Optional[list[Optional[list[Optional[bool]]]]] = None,
        labels: Optional[list[str]] = None,
    ) -> list[float]:
        """
        Calibrate a list of probabilities.

        Parameters
        ----------
        probabilities:
            Raw model probabilities.
        signal_directions_batch:
            One signal_directions list per probability, or None.
        labels:
            One label per probability, or None.

        Returns
        -------
        List of calibrated probabilities.
        """
        n = len(probabilities)
        sdb = signal_directions_batch or ([None] * n)
        lbls = labels or ([""] * n)

        results: list[float] = []
        for idx, (raw_p, sigs, lbl) in enumerate(zip(probabilities, sdb, lbls)):
            try:
                results.append(
                    self.calibrate(raw_p, signal_directions=sigs, label=lbl)
                )
            except Exception as exc:
                logger.error(
                    "calibrate_batch: error at index %d label=%s: %s",
                    idx,
                    lbl,
                    exc,
                )
                results.append(float(max(0.05, min(0.95, raw_p))))

        return results


_default_calibrator = Calibrator()


def calibrate(
    p: float,
    signal_directions: Optional[list[Optional[bool]]] = None,
    label: str = "",
    alpha: float = 1.3,
) -> float:
    """
    Module-level convenience wrapper around Calibrator.calibrate().
    Creates a one-shot Calibrator with the supplied alpha.
    """
    cal = Calibrator(alpha=alpha)
    return cal.calibrate(p, signal_directions=signal_directions, label=label)