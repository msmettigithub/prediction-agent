import logging
import math
import os

logger = logging.getLogger(__name__)

try:
    import config
    CALIBRATION_SHARPNESS = getattr(config, 'CALIBRATION_SHARPNESS', 1.5)
except ImportError:
    CALIBRATION_SHARPNESS = 1.5

SHARPEN_K = float(os.environ.get('CALIBRATION_SHARPNESS', CALIBRATION_SHARPNESS))
CLAMP_LOW = 0.04
CLAMP_HIGH = 0.96
PRE_LOGIT_CLIP_LOW = 0.02
PRE_LOGIT_CLIP_HIGH = 0.98
LOGIT_CAP = 4.0


def sharpen_probability(p, k=None):
    if k is None:
        k = SHARPEN_K
    try:
        p_f = float(p)
    except (TypeError, ValueError):
        logger.warning("sharpen_probability received non-numeric input: %r, returning 0.5", p)
        return 0.5

    if not math.isfinite(p_f):
        logger.warning("sharpen_probability received non-finite input: %r, returning 0.5", p_f)
        return 0.5

    p_pre = max(PRE_LOGIT_CLIP_LOW, min(PRE_LOGIT_CLIP_HIGH, p_f))

    try:
        logit = math.log(p_pre / (1.0 - p_pre))
    except (ValueError, ZeroDivisionError):
        logger.warning("logit computation failed for p=%r, returning clamped input", p_pre)
        return max(CLAMP_LOW, min(CLAMP_HIGH, p_f))

    stretched_logit = k * logit

    stretched_logit = max(-LOGIT_CAP, min(LOGIT_CAP, stretched_logit))

    try:
        p_sharp = 1.0 / (1.0 + math.exp(-stretched_logit))
    except OverflowError:
        p_sharp = 0.0 if stretched_logit < 0 else 1.0

    p_out = max(CLAMP_LOW, min(CLAMP_HIGH, p_sharp))

    logger.info(
        "sharpen_probability: pre_sharpen=%.4f logit=%.4f k=%.2f stretched_logit=%.4f post_sharpen=%.4f",
        p_f, logit, k, stretched_logit, p_out
    )

    return p_out


def calibrate(p, k=None):
    if k is None:
        k = SHARPEN_K
    try:
        p_f = float(p)
    except (TypeError, ValueError):
        logger.warning("calibrate received non-numeric input: %r, returning 0.5", p)
        return 0.5

    if not math.isfinite(p_f):
        logger.warning("calibrate received non-finite input: %r, returning 0.5", p_f)
        return 0.5

    pre_sharpen = p_f
    p_out = sharpen_probability(p_f, k=k)

    logger.info(
        "calibrate: input=%.4f pre_sharpening=%.4f post_sharpening=%.4f k=%.2f [RL_TRACKING]",
        p_f, pre_sharpen, p_out, k
    )

    return p_out


def adjust_probability(p, k=None):
    if k is None:
        k = SHARPEN_K
    try:
        p_f = float(p)
    except (TypeError, ValueError):
        logger.warning("adjust_probability received non-numeric input: %r, returning 0.5", p)
        return 0.5

    if not math.isfinite(p_f):
        logger.warning("adjust_probability received non-finite input: %r, returning 0.5", p_f)
        return 0.5

    pre_sharpen = p_f
    p_out = sharpen_probability(p_f, k=k)

    logger.info(
        "adjust_probability: input=%.4f pre_sharpening=%.4f post_sharpening=%.4f k=%.2f [RL_TRACKING]",
        p_f, pre_sharpen, p_out, k
    )

    return p_out


class Calibrator:
    def __init__(self, k=None):
        self.k = k if k is not None else SHARPEN_K
        logger.info("Calibrator initialized with sharpness k=%.2f", self.k)

    def calibrate(self, p):
        return calibrate(p, k=self.k)

    def adjust_probability(self, p):
        return adjust_probability(p, k=self.k)

    def sharpen_probability(self, p):
        return sharpen_probability(p, k=self.k)

    def process(self, p):
        try:
            p_f = float(p)
        except (TypeError, ValueError):
            logger.warning("Calibrator.process received non-numeric input: %r, returning 0.5", p)
            return 0.5

        if not math.isfinite(p_f):
            logger.warning("Calibrator.process received non-finite input: %r, returning 0.5", p_f)
            return 0.5

        pre_sharpen = p_f
        p_out = self.sharpen_probability(p_f)

        logger.info(
            "Calibrator.process: input=%.4f pre_sharpening=%.4f post_sharpening=%.4f k=%.2f [RL_TRACKING]",
            p_f, pre_sharpen, p_out, self.k
        )

        return p_out


_default_calibrator = Calibrator()


def process(p):
    return _default_calibrator.process(p)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_cases = [0.45, 0.50, 0.55, 0.60, 0.70, 0.80, 0.90, 0.02, 0.98, 0.0, 1.0]
    print(f"Sharpness k={SHARPEN_K}")
    print(f"{'Input':>10} {'Output':>10} {'Delta':>10}")
    for p in test_cases:
        try:
            out = calibrate(p)
            print(f"{p:>10.4f} {out:>10.4f} {out - p:>+10.4f}")
        except Exception as e:
            print(f"{p:>10} ERROR: {e}")