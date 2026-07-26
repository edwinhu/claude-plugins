#!/usr/bin/env -S uv run python3
"""
Data-quality detectors for holdings panels (Thomson/Refinitiv S12 & S34, SEC 13F, N-PX).

Stdlib only. Every detector takes a sequence of record dicts -- exactly what
`df.to_dict("records")` gives you -- so it runs in CI with no dependencies and on a
real parquet panel through a one-line adapter.

Each detector answers a question a *reviewer* would ask of an ownership panel, and each
is grounded in a defect that was either measured on the mirror panel or documented by
WRDS Research. See `references/tfn-ownership.md` -> "Known Data Defects" for the
citations and for what WRDS says the fix is (in several cases: there is no clean fix).

The detectors are firm-agnostic by construction. AAPL (permno 14593) is where these
defects were found -- and, not coincidentally, the running example in WRDS's own splits
note -- but nothing here is specialized to it.

Conventions
-----------
Every detector returns a list of `Finding`. An empty list means "clean". Callers assert
on the list, so a detector never raises on ordinary bad data -- only on malformed input.

Periods are `datetime.date` (quarter-end) or ISO "YYYY-MM-DD" strings; both are accepted
and normalized. Records with a None metric are skipped, not treated as zero -- a missing
quarter and a zero quarter are different defects and conflating them hides both.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

__all__ = [
    "Finding",
    "detect_flat_owner_share_swing",
    "detect_seasonal_alternation",
    "detect_split_factor_ratio",
    "detect_unit_discontinuity",
    "detect_coverage_end",
    "detect_owner_dropout",
    "detect_zero_row_cohort",
    "COMMON_SPLIT_FACTORS",
]

# Split factors common enough in US equities that a holdings ratio landing on one is
# evidence of a mis-applied adjustment rather than real trading. Squares are included
# because double-adjustment is a documented Thomson failure mode: WRDS's splits note
# shows AAPL shares adjusted 7x twice for an erroneous "1-to-49 split".
COMMON_SPLIT_FACTORS = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 10.0, 20.0, 28.0)


@dataclass
class Finding:
    """One flagged observation. `kind` is stable and machine-matchable."""

    kind: str
    entity: Any
    period: Any
    detail: str
    metrics: dict = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"[{self.kind}] entity={self.entity} period={self.period}: {self.detail}"


# --------------------------------------------------------------------------- helpers


def _as_date(value: Any) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        return _dt.date.fromisoformat(value[:10])
    raise TypeError(f"cannot interpret {value!r} as a date")


def _grouped(
    records: Iterable[dict], entity_col: str, period_col: str
) -> dict[Any, list[dict]]:
    """Group by entity, each group sorted by period. Later duplicates win."""
    out: dict[Any, dict[_dt.date, dict]] = {}
    for rec in records:
        entity = rec.get(entity_col)
        period = _as_date(rec[period_col])
        out.setdefault(entity, {})[period] = rec
    return {
        entity: [by_period[p] for p in sorted(by_period)]
        for entity, by_period in out.items()
    }


def _ratio(current: float, previous: float) -> float | None:
    """Fold-change, orientation-free: always >= 1.0, or None if undefined."""
    if previous is None or current is None:
        return None
    if previous == 0 or current == 0:
        return None
    lo, hi = sorted((abs(previous), abs(current)))
    return hi / lo


def _pct_change(current: float, previous: float) -> float | None:
    if previous in (None, 0) or current is None:
        return None
    return (current - previous) / abs(previous)


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile. `sorted_values` must be sorted and non-empty."""
    if not sorted_values:
        raise ValueError("empty sequence")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


# ------------------------------------------------------------------- the detectors


def detect_flat_owner_share_swing(
    records: Sequence[dict],
    *,
    entity_col: str = "permno",
    period_col: str = "rdate",
    shares_col: str = "io_total",
    owners_col: str = "numowners",
    share_swing: float = 0.20,
    owner_tolerance: float = 0.05,
) -> list[Finding]:
    """Shares move violently while the owner count barely moves.

    THE signature of a units/adjustment defect rather than a coverage defect. If
    institutions actually left, the owner count would fall with the shares. When 2,200
    managers keep reporting and the aggregate share count collapses 4.5x, the shares are
    being *rescaled*, not sold.

    This is the detector for the undiagnosed AAPL defect (mirror `inst_own.parquet`,
    permno 14593): io_total means of 1.41e10 (Mar) / 3.11e9 (Jun) / 3.31e9 (Sep) /
    1.41e10 (Dec) against numowners flat at 2,195-2,427.

    A holding that legitimately doubles usually brings new holders with it, so the
    tolerance on the owner side is deliberately tight (5%) while the share side is loose
    (20%) -- we want to catch rescaling, not trading.
    """
    findings: list[Finding] = []
    for entity, rows in _grouped(records, entity_col, period_col).items():
        for prev, cur in zip(rows, rows[1:]):
            s_prev, s_cur = prev.get(shares_col), cur.get(shares_col)
            o_prev, o_cur = prev.get(owners_col), cur.get(owners_col)
            if None in (s_prev, s_cur, o_prev, o_cur):
                continue
            s_change = _pct_change(s_cur, s_prev)
            o_change = _pct_change(o_cur, o_prev)
            if s_change is None or o_change is None:
                continue
            if abs(s_change) >= share_swing and abs(o_change) <= owner_tolerance:
                findings.append(
                    Finding(
                        kind="flat_owner_share_swing",
                        entity=entity,
                        period=_as_date(cur[period_col]),
                        detail=(
                            f"{shares_col} moved {s_change:+.1%} while {owners_col} "
                            f"moved only {o_change:+.1%} -- shares rescaled, not traded"
                        ),
                        metrics={
                            "shares_pct_change": s_change,
                            "owners_pct_change": o_change,
                            "shares_prev": s_prev,
                            "shares_cur": s_cur,
                            "owners_prev": o_prev,
                            "owners_cur": o_cur,
                            "fold_change": _ratio(s_cur, s_prev),
                        },
                    )
                )
    return findings


def detect_seasonal_alternation(
    records: Sequence[dict],
    *,
    entity_col: str = "permno",
    period_col: str = "rdate",
    metric_col: str = "io_total",
    min_quarters: int = 8,
    ratio_threshold: float = 1.5,
) -> list[Finding]:
    """A metric whose level depends on which calendar quarter-end it is.

    Real ownership has no reason to care whether the quarter ends in June or December.
    When it does, a vintage/adjustment artifact is keyed to the quarter-end month.

    Compares the mean of each quarter-end month against the pooled mean of the others and
    flags the entity when the highest-month/lowest-month ratio clears `ratio_threshold`.
    Reported once per entity, not per quarter -- seasonality is a property of the series.
    """
    findings: list[Finding] = []
    for entity, rows in _grouped(records, entity_col, period_col).items():
        by_month: dict[int, list[float]] = {}
        for rec in rows:
            value = rec.get(metric_col)
            if value is None:
                continue
            by_month.setdefault(_as_date(rec[period_col]).month, []).append(float(value))
        usable = {m: v for m, v in by_month.items() if v}
        if len(usable) < 2 or sum(len(v) for v in usable.values()) < min_quarters:
            continue
        means = {m: sum(v) / len(v) for m, v in usable.items()}
        hi_month = max(means, key=lambda m: means[m])
        lo_month = min(means, key=lambda m: means[m])
        if means[lo_month] <= 0:
            continue
        ratio = means[hi_month] / means[lo_month]
        if ratio >= ratio_threshold:
            findings.append(
                Finding(
                    kind="seasonal_alternation",
                    entity=entity,
                    period=None,
                    detail=(
                        f"{metric_col} mean varies {ratio:.2f}x by quarter-end month "
                        f"(month {hi_month}={means[hi_month]:.3g} vs "
                        f"month {lo_month}={means[lo_month]:.3g}); "
                        f"ownership has no calendar seasonality this large"
                    ),
                    metrics={
                        "ratio": ratio,
                        "means_by_month": means,
                        "high_month": hi_month,
                        "low_month": lo_month,
                        "n_quarters": sum(len(v) for v in usable.values()),
                    },
                )
            )
    return findings


def detect_split_factor_ratio(
    records: Sequence[dict],
    *,
    entity_col: str = "permno",
    period_col: str = "rdate",
    shares_col: str = "io_total",
    owners_col: str | None = "numowners",
    factors: Sequence[float] = COMMON_SPLIT_FACTORS,
    tolerance: float = 0.03,
    include_squares: bool = True,
) -> list[Finding]:
    """A quarter-over-quarter share ratio that lands on a split factor -- or its square.

    Distinguishes a mis-applied adjustment from ordinary trading: real flows do not
    produce a clean 7.00x. Squares are checked because double-adjustment is documented:
    WRDS's splits note shows S12 reporting 226,331 shares where 32,333 was correct --
    4,619 x 7 x 7, "an erroneous 1-to-49 split".

    When `owners_col` is given, the owner count must be roughly flat for the ratio to
    count as evidence -- a 2x share jump accompanied by 2x the holders is a real event.
    Pass `owners_col=None` to check the ratio alone.
    """
    targets: list[tuple[float, str]] = [(float(f), "split") for f in factors]
    if include_squares:
        targets += [(float(f) ** 2, "double-adjusted") for f in factors]

    findings: list[Finding] = []
    for entity, rows in _grouped(records, entity_col, period_col).items():
        for prev, cur in zip(rows, rows[1:]):
            ratio = _ratio(cur.get(shares_col), prev.get(shares_col))
            if ratio is None or ratio < 1.5:
                continue
            if owners_col is not None:
                o_change = _pct_change(cur.get(owners_col), prev.get(owners_col))
                if o_change is None or abs(o_change) > 0.10:
                    continue
            for target, label in targets:
                if abs(ratio - target) / target <= tolerance:
                    findings.append(
                        Finding(
                            kind="split_factor_ratio",
                            entity=entity,
                            period=_as_date(cur[period_col]),
                            detail=(
                                f"{shares_col} changed by {ratio:.3f}x, within "
                                f"{tolerance:.0%} of {target:g}x ({label}) -- "
                                f"consistent with a mis-applied share adjustment"
                            ),
                            metrics={
                                "ratio": ratio,
                                "matched_factor": target,
                                "match_kind": label,
                                "shares_prev": prev.get(shares_col),
                                "shares_cur": cur.get(shares_col),
                            },
                        )
                    )
                    break
    return findings


def detect_unit_discontinuity(
    records: Sequence[dict],
    *,
    period_col: str = "rdate",
    value_col: str = "value",
    min_median_ratio: float = 10.0,
    dispersion_ratio: float = 3.0,
    min_obs_per_period: int = 20,
) -> list[Finding]:
    """A period boundary where a value column changes *units*.

    Works on the cross-section, not one entity: compares the distribution of `value_col`
    in each period against the previous period. A units change moves the whole
    distribution at once, which a real economic shock does not.

    Also reports whether the break is *clean*. The measured Form 13F break at 2023Q1
    (thousands -> whole dollars) is not: median 442x, p90 494x, but p10 only 38x. That
    spread means filers converted at different times, so the post-break population is
    mixed and **no scalar repairs it**. When p90/p10 of the per-decile shift exceeds
    `dispersion_ratio`, the finding is marked `clean_break=False`, which is the signal
    that any sum or mean of `value_col` spanning the boundary is meaningless rather than
    merely rescaled.
    """
    by_period: dict[_dt.date, list[float]] = {}
    for rec in records:
        value = rec.get(value_col)
        if value is None:
            continue
        by_period.setdefault(_as_date(rec[period_col]), []).append(float(value))

    findings: list[Finding] = []
    periods = sorted(by_period)
    for prev_p, cur_p in zip(periods, periods[1:]):
        prev_v = sorted(v for v in by_period[prev_p] if v > 0)
        cur_v = sorted(v for v in by_period[cur_p] if v > 0)
        if len(prev_v) < min_obs_per_period or len(cur_v) < min_obs_per_period:
            continue
        q = {
            name: (_quantile(cur_v, p) / _quantile(prev_v, p))
            for name, p in (("p10", 0.10), ("median", 0.50), ("p90", 0.90))
            if _quantile(prev_v, p) > 0
        }
        median_ratio = q.get("median")
        if median_ratio is None or median_ratio < min_median_ratio:
            continue
        spread = (
            max(q.values()) / min(q.values()) if min(q.values(), default=0) > 0 else None
        )
        clean = spread is not None and spread <= dispersion_ratio
        findings.append(
            Finding(
                kind="unit_discontinuity",
                entity=None,
                period=cur_p,
                detail=(
                    f"{value_col} median shifted {median_ratio:.0f}x at {cur_p} "
                    f"(p10={q.get('p10', float('nan')):.0f}x, "
                    f"p90={q.get('p90', float('nan')):.0f}x); "
                    + (
                        "uniform shift -- a scalar rescale may be defensible"
                        if clean
                        else "NON-UNIFORM: population is mixed, no scalar repairs it; "
                        "do not sum or average across this boundary"
                    )
                ),
                metrics={
                    "quantile_ratios": q,
                    "median_ratio": median_ratio,
                    "dispersion": spread,
                    "clean_break": clean,
                    "prev_period": prev_p,
                    "n_prev": len(prev_v),
                    "n_cur": len(cur_v),
                },
            )
        )
    return findings


def detect_coverage_end(
    records: Sequence[dict],
    *,
    period_col: str = "rdate",
    expected_through: Any,
    entity_col: str | None = None,
    max_gap_days: int = 100,
) -> list[Finding]:
    """The panel stops before it should.

    A silently truncated series is the most dangerous defect in this file, because every
    downstream statistic still computes. S12 in the mirror ends 2024-12-31 with no 2025
    coverage; WRDS separately froze the S34 web query at 2019Q2 during the 2022 feed
    outage. Both are invisible unless asserted.

    With `entity_col` set, checks each entity's last period; otherwise checks the panel.
    """
    expected = _as_date(expected_through)
    findings: list[Finding] = []

    def _check(entity: Any, periods: list[_dt.date]) -> None:
        if not periods:
            return
        last = max(periods)
        gap = (expected - last).days
        if gap > max_gap_days:
            findings.append(
                Finding(
                    kind="coverage_end",
                    entity=entity,
                    period=last,
                    detail=(
                        f"coverage ends {last}, {gap} days short of the expected "
                        f"{expected}; downstream statistics will still compute"
                    ),
                    metrics={
                        "last_period": last,
                        "expected_through": expected,
                        "gap_days": gap,
                    },
                )
            )

    if entity_col is None:
        _check(None, [_as_date(r[period_col]) for r in records])
    else:
        for entity, rows in _grouped(records, entity_col, period_col).items():
            _check(entity, [_as_date(r[period_col]) for r in rows])
    return findings


def detect_owner_dropout(
    records: Sequence[dict],
    *,
    entity_col: str = "permno",
    period_col: str = "rdate",
    owners_col: str = "numowners",
    drop_threshold: float = 0.50,
) -> list[Finding]:
    """The owner count itself collapses -- a coverage/feed defect, not a units defect.

    The complement of `detect_flat_owner_share_swing`, and the two together classify a
    break: shares move but owners do not -> adjustment bug; owners move too -> the feed
    dropped filers.

    WRDS documented exactly this in Feb 2022: institutions reporting AAPL, IBM and MSFT
    fell from 1,500+ to under 500 in 2019Q3-Q4, and 1,386 of 12,224 stocks lost >10% of
    institutional ownership (median -48%). Refinitiv patched it in April 2022 -- so this
    detector should be run against *current* extracts, since an unpatched local mirror
    keeps the defect.
    """
    findings: list[Finding] = []
    for entity, rows in _grouped(records, entity_col, period_col).items():
        for prev, cur in zip(rows, rows[1:]):
            change = _pct_change(cur.get(owners_col), prev.get(owners_col))
            if change is not None and change <= -drop_threshold:
                findings.append(
                    Finding(
                        kind="owner_dropout",
                        entity=entity,
                        period=_as_date(cur[period_col]),
                        detail=(
                            f"{owners_col} fell {change:.1%} "
                            f"({prev.get(owners_col)} -> {cur.get(owners_col)}) -- "
                            f"filers missing from the feed"
                        ),
                        metrics={
                            "owners_pct_change": change,
                            "owners_prev": prev.get(owners_col),
                            "owners_cur": cur.get(owners_col),
                        },
                    )
                )
    return findings


def detect_zero_row_cohort(
    records: Sequence[dict],
    *,
    cohort_col: str,
    rows_col: str = "n_rows",
    period_col: str | None = None,
    min_docs: int = 10,
    zero_rate_threshold: float = 0.90,
) -> list[Finding]:
    """A whole cohort of documents that parsed to zero rows.

    A parser that silently yields nothing looks identical to a filer holding nothing.
    The Windows-1252 encoding bug did exactly this: 7,023 filings / 2,628,463 rows / 768
    institutions dropped to zero across all 38 quarters, concentrated by filing agent and
    stepping 3.47x at 2023Q3 (5.54% of holdings lost after, 1.60% before).

    Group by whatever cohort could carry an encoding or vendor quirk -- filing agent,
    declared charset, parser version -- and flag any cohort that is almost entirely
    empty. With `period_col`, also reports the per-period zero rate so a *time-varying*
    step (the 2023Q3 jump) is visible rather than averaged away.
    """
    cohorts: dict[Any, list[dict]] = {}
    for rec in records:
        cohorts.setdefault(rec.get(cohort_col), []).append(rec)

    findings: list[Finding] = []
    for cohort, rows in sorted(cohorts.items(), key=lambda kv: str(kv[0])):
        if len(rows) < min_docs:
            continue
        zeros = [r for r in rows if not r.get(rows_col)]
        rate = len(zeros) / len(rows)
        if rate < zero_rate_threshold:
            continue
        metrics: dict[str, Any] = {
            "zero_rate": rate,
            "n_docs": len(rows),
            "n_zero": len(zeros),
        }
        if period_col is not None:
            per_period: dict[Any, list[dict]] = {}
            for r in rows:
                per_period.setdefault(_as_date(r[period_col]), []).append(r)
            metrics["zero_rate_by_period"] = {
                p: sum(1 for r in rs if not r.get(rows_col)) / len(rs)
                for p, rs in sorted(per_period.items())
            }
        findings.append(
            Finding(
                kind="zero_row_cohort",
                entity=cohort,
                period=None,
                detail=(
                    f"cohort {cohort_col}={cohort!r}: {len(zeros)}/{len(rows)} "
                    f"documents parsed to zero rows ({rate:.1%}) -- "
                    f"indistinguishable from 'held nothing' unless asserted"
                ),
                metrics=metrics,
            )
        )
    return findings


def from_dataframe(df: Any, detector: Callable[..., list[Finding]], **kwargs: Any):
    """Adapter: run any detector against a pandas/polars frame.

    Kept trivial on purpose -- the detectors take records so the test suite needs no
    dataframe library, and real callers pay one conversion.
    """
    records = (
        df.to_dicts() if hasattr(df, "to_dicts") else df.to_dict("records")
    )
    return detector(records, **kwargs)
