"""Averaging trials, with checks a student can redo by hand on the printed form.

Each trial gives one value; the result is their mean. The spread is the highest minus the lowest, as a fraction of
the mean. Fewer than 3 trials, or a spread over 15%, makes the average not good enough: a run uses it, but its
result stays UNCALIBRATED. A trial more than twice the average distance from the mean is flagged as an outlier; it
is never dropped. An outlier needs at least 5 trials: one trial's distance from the mean is at most half the sum of
all the trials' distances (the distances above the mean balance those below), so at most n/2 times the average
distance, and n/2 is more than 2 only from 5 trials up.
"""

from dataclasses import dataclass

MIN_TRIALS = 3
MAX_SPREAD = 0.15  # (highest - lowest) / mean
OUTLIER_DISTANCES = 2.0  # how many mean absolute deviations from the mean flag a trial


@dataclass(frozen=True, kw_only=True)
class Summary:
    mean: float
    low: float
    high: float
    spread: float  # (high - low) / mean: 0.22 is 22%
    n: int
    outliers: list[int]  # 0-based indexes (into the values) of the trials that look off
    trials: list[int]  # 0-based: the trial each value came from (a size trial may only weigh, so give no diameter)
    warnings: list[str]  # plain sentences, trials numbered from 1
    good: bool  # at least 3 trials, spread at most 15%


def summary(values, trials=None) -> Summary:
    """The mean, lowest, highest, spread and warnings of one value per trial. `trials` (0-based, one per value) are
    the trials the values came from, for the warnings' numbers, when not every trial gives one (a size trial may
    only weigh); by default the values are trials 1, 2, 3 ..."""
    values = [float(v) for v in values]
    if not values:
        raise ValueError("summary needs at least one trial")
    n = len(values)
    mean = sum(values) / n
    low, high = min(values), max(values)
    spread = (high - low) / abs(mean) if mean else (0.0 if high == low else float("inf"))
    distance = sum(abs(v - mean) for v in values) / n
    outliers = [i for i, v in enumerate(values) if abs(v - mean) > OUTLIER_DISTANCES * distance]

    warnings = []
    if n < MIN_TRIALS:
        warnings.append(f"take at least {MIN_TRIALS} trials (this has {n}): "
                        "a run uses it, but its result stays UNCALIBRATED until then")
    if spread > MAX_SPREAD:
        warnings.append(f"your trials disagree by {spread:.0%}: measure again or take more trials")
    number = list(trials) if trials is not None else list(range(n))
    if len(number) != n:
        raise ValueError("summary needs one trial number per value")
    warnings += [f"trial {number[i] + 1} looks off: check it" for i in outliers]
    return Summary(mean=mean, low=low, high=high, spread=spread, n=n, outliers=outliers, trials=number,
                   warnings=warnings, good=n >= MIN_TRIALS and spread <= MAX_SPREAD)


def why(s: Summary) -> str:
    """Why a summary isn't good enough (a run uses it, but its result stays UNCALIBRATED), in a few words; empty when
    it is good."""
    reasons = []
    if s.n < MIN_TRIALS:
        reasons.append(f"{s.n} trial{'s' if s.n != 1 else ''}: take at least {MIN_TRIALS}")
    if s.spread > MAX_SPREAD:
        reasons.append(f"trials disagree by {s.spread:.0%}")
    return "; ".join(reasons)
