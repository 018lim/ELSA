"""몬테카를로 평가 엔진 (streamlit 의존 없음).

위험중립 기하브라운운동 + 촐레스키 상관, 연 252영업일(월 21영업일) 일별 격자,
대조변수(antithetic) 분산 축소, 배치 처리로 공정가치와 상환 통계를 계산한다.
경로 전체를 메모리에 두지 않고 한 달(21스텝)씩 생성하며 워스트 퍼포머의
월말 값·월중 최저·최고만 넘겨 메모리 사용을 작게 유지한다.

- price(request): 상품 유형에 맞는 평가 함수를 골라 PricingResult를 돌려준다
- price_autocall(...): 자동조기상환형(일반·월지급식·리자드, 최대 손실률) 평가. 적정 쿠폰을 함께 계산
- price_knockout(...): 녹아웃형 평가(최대 손실률 포함). 적정 참여율을 함께 계산
- _iter_batches(...): 배치별로 월 단위 워스트 퍼포머 통계를 내보내는 경로 생성기

적정 쿠폰: 지급액이 쿠폰에 선형이라 경로별로 쿠폰 무관 현재가치 A와 쿠폰 1.0당
현재가치 B를 모아 price = A + c·B, fair = (target − A) / B로 바로 구한다.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from elsa.schemas import (
    AutocallTerms,
    KnockoutTerms,
    MarketData,
    PricingRequest,
    PricingResult,
    SimSettings,
)

STEPS_PER_MONTH = 21
DT = 1.0 / 252.0

MonthStats = tuple[int, np.ndarray, np.ndarray, np.ndarray]  # (월, 월말 워스트, 월중 최저, 월중 최고)


def price(request: PricingRequest) -> PricingResult:
    """상품 유형에 따라 평가 함수를 호출한다."""
    ts = request.term_sheet
    if isinstance(ts, AutocallTerms):
        return price_autocall(ts, request.market, request.sim)
    return price_knockout(ts, request.market, request.sim)


# ---------------------------------------------------------------------------
# 경로 생성
# ---------------------------------------------------------------------------


def _iter_batches(
    market: MarketData, months: int, sim: SimSettings
) -> Iterator[tuple[int, Iterator[MonthStats]]]:
    """배치마다 (경로 수, 월별 통계 생성기)를 내보낸다.

    각 배치의 앞쪽 절반과 뒤쪽 절반은 서로 대조변수 쌍이다 (i ↔ i + n/2).
    같은 시드·경로 수·만기·자산 수면 같은 난수를 쓰므로 민감도 분석에서 공통 난수가 된다.
    """
    vols = np.asarray(market.vols, dtype=float)
    divs = np.asarray(market.divs, dtype=float)
    chol = np.linalg.cholesky(np.asarray(market.corr, dtype=float))
    drift = (market.rate - divs - 0.5 * vols**2) * DT
    vol_dt = vols * np.sqrt(DT)
    chol32, drift32, vol_dt32 = (x.astype(np.float32) for x in (chol, drift, vol_dt))
    n_assets = len(vols)

    rng = np.random.default_rng(sim.seed)
    total_pairs = (sim.n_paths + 1) // 2
    pairs_per_batch = max(1, sim.batch_size // 2)

    done = 0
    while done < total_pairs:
        n_half = min(pairs_per_batch, total_pairs - done)
        done += n_half

        def months_iter(n_half: int = n_half) -> Iterator[MonthStats]:
            # 배열 배치는 (자산, 경로, 스텝). 자산 수가 1~3개라 자산 축 연산은 파이썬 반복으로 하고,
            # 큰 축(경로·스텝)은 연속 메모리로 두어 빠르게 계산한다. 난수·증분은 float32.
            log_s = [np.zeros((2 * n_half, 1)) for _ in range(n_assets)]
            for m in range(1, months + 1):
                raw = rng.standard_normal((n_assets, n_half, STEPS_PER_MONTH), dtype=np.float32)
                worst_log = None
                for i in range(n_assets):
                    z = sum(chol32[i, j] * raw[j] for j in range(i + 1))  # 촐레스키 하삼각
                    inc = np.concatenate([drift32[i] + vol_dt32[i] * z, drift32[i] - vol_dt32[i] * z])
                    path = log_s[i] + np.cumsum(inc, axis=1)
                    log_s[i] = path[:, -1:]
                    worst_log = path if worst_log is None else np.minimum(worst_log, path)
                # exp는 단조 증가라 로그에서 최저·최고를 구한 뒤 변환해도 같다
                yield (
                    m,
                    np.exp(worst_log[:, -1]),
                    np.exp(worst_log.min(axis=1)),
                    np.exp(worst_log.max(axis=1)),
                )

        yield 2 * n_half, months_iter()


class _PairStats:
    """대조변수 쌍 평균 기준으로 A, B의 평균·분산·공분산을 누적한다."""

    def __init__(self) -> None:
        self.n = 0
        self.s = np.zeros(5)  # ΣA, ΣB, ΣA², ΣB², ΣAB (쌍 평균 기준)

    def add(self, a: np.ndarray, b: np.ndarray) -> None:
        h = len(a) // 2
        pa = 0.5 * (a[:h] + a[h:])
        pb = 0.5 * (b[:h] + b[h:])
        self.n += h
        self.s += [pa.sum(), pb.sum(), (pa * pa).sum(), (pb * pb).sum(), (pa * pb).sum()]

    def mean(self) -> tuple[float, float]:
        return self.s[0] / self.n, self.s[1] / self.n

    def stderr(self, c: float) -> float:
        """A + c·B의 표준오차."""
        n = self.n
        ma, mb = self.mean()
        var_a = self.s[2] / n - ma * ma
        var_b = self.s[3] / n - mb * mb
        cov = self.s[4] / n - ma * mb
        var = max(var_a + c * c * var_b + 2 * c * cov, 0.0) * n / max(n - 1, 1)
        return float(np.sqrt(var / n))


# ---------------------------------------------------------------------------
# 자동조기상환형
# ---------------------------------------------------------------------------


def price_autocall(ts: AutocallTerms, market: MarketData, sim: SimSettings) -> PricingResult:
    """자동조기상환형(일반·월지급식·리자드) 평가.

    월지급식·리자드는 쿠폰을 조건을 채운 날 바로 지급하므로(b에 그때그때 더함), 조기상환·만기 때
    누적 쿠폰을 따로 주지 않는다. 리자드 상품은 모든 평가일에 "그 회차 쿠폰(연 쿠폰 × 평가 주기 ÷ 12)"만
    지급한다: 리자드 배리어가 있는 회차는 워스트 ≥ min(리자드 배리어, 상환 배리어)일 때,
    없는 회차는 워스트 ≥ 상환 배리어(= 상환)일 때.
    """
    period = ts.obs_period_months
    n_obs = ts.n_obs
    months = ts.maturity_months
    barriers = np.asarray(ts.barriers, dtype=float)
    obs_index = {(k + 1) * period: k for k in range(n_obs)}
    coupon_unit = period / 12.0  # 연 쿠폰 1.0당 회차별 쿠폰
    monthly = ts.is_monthly
    lizard = ts.is_lizard
    paid_as_you_go = monthly or lizard
    ki_mode = ts.ki_mode
    ki_barrier = ts.ki_barrier if ki_mode != "none" else None
    dummy = ts.dummy_coupon and ki_mode != "none" and not paid_as_you_go
    floor = ts.principal_floor  # 만기 최저 상환 비율 (최대 손실률 반영)

    stats = _PairStats()
    redeem_counts = np.zeros(n_obs)
    loss_count = 0
    loss_sum = 0.0
    life_sum = 0.0
    ki_unredeemed = 0
    monthly_coupon_sum = 0.0
    lizard_coupon_sum = 0.0
    total = 0

    for n, months_iter in _iter_batches(market, months, sim):
        total += n
        alive = np.ones(n, dtype=bool)
        ki_hit = np.zeros(n, dtype=bool)
        a = np.zeros(n)
        b = np.zeros(n)

        for m, w_end, w_min, _ in months_iter:
            df = np.exp(-market.rate * m / 12.0)
            if ki_mode == "daily":
                ki_hit |= alive & (w_min < ki_barrier)
            elif ki_mode == "monthly":  # 매월 말일(21번째 영업일) 종가만 확인. 만기일도 월말이라 포함
                ki_hit |= alive & (w_end < ki_barrier)
            if monthly:
                pay = alive & (w_end >= ts.monthly_coupon_barrier)
                b[pay] += df / 12.0
                monthly_coupon_sum += pay.sum()

            k = obs_index.get(m)
            if k is None:
                continue

            if lizard:  # 상환 판정 전에, 아직 살아 있는 경로에 이 회차 쿠폰 지급
                l_k = ts.lizard_at(k)
                pay = alive & (w_end >= (barriers[k] if l_k is None else min(l_k, barriers[k])))
                b[pay] += df * coupon_unit
                lizard_coupon_sum += pay.sum()

            if k < n_obs - 1:
                hit = alive & (w_end >= barriers[k])
                a[hit] += df
                if not paid_as_you_go:
                    b[hit] += df * coupon_unit * (k + 1)
                redeem_counts[k] += hit.sum()
                life_sum += hit.sum() * m
                alive &= ~hit
                continue

            # 만기
            if ki_mode == "maturity":
                ki_hit |= alive & (w_end < ki_barrier)
            above = alive & (w_end >= barriers[-1])
            protected = alive & ~above & ~ki_hit if ki_mode != "none" else np.zeros(n, dtype=bool)
            full = above | (protected if dummy else np.zeros(n, dtype=bool))
            # 손실 구간: 워스트 비율만큼 상환하되 최대 손실률 한도(최저 상환 비율)까지만 잃는다
            exposed = alive & ~above & ~protected
            payout = np.maximum(w_end, floor)
            loss = exposed & (payout < 1.0)  # 상환액이 원금 미만일 때만 손실로 집계
            principal_only = protected & ~full

            a[full | principal_only] += df
            if not paid_as_you_go:
                b[full] += df * coupon_unit * n_obs
            a[exposed] += df * payout[exposed]

            redeem_counts[n_obs - 1] += (full | principal_only | (exposed & ~loss)).sum()
            loss_count += loss.sum()
            loss_sum += payout[loss].sum()
            life_sum += alive.sum() * m
            ki_unredeemed += (alive & ki_hit).sum()

        stats.add(a, b)

    mean_a, mean_b = stats.mean()
    fair = (sim.target - mean_a) / mean_b if mean_b > 0 else None
    c = ts.coupon_annual
    price_value = mean_a + c * mean_b if c is not None else None
    se_c = c if c is not None else (fair if fair is not None else 0.0)

    return PricingResult(
        product_type="autocall",
        price=price_value,
        stderr=stats.stderr(se_c),
        target=sim.target,
        fair_coupon_annual=fair,
        n_paths=total,
        obs_months=[(k + 1) * period for k in range(n_obs)],
        redemption_probs=(redeem_counts / total).tolist(),
        early_redemption_prob=float(redeem_counts[:-1].sum() / total),
        maturity_loss_prob=loss_count / total,
        avg_loss_redemption=(loss_sum / loss_count) if loss_count else None,
        expected_life_months=life_sum / total,
        ki_unredeemed_prob=ki_unredeemed / total,
        avg_monthly_coupons=(monthly_coupon_sum / total) if monthly else None,
        avg_lizard_coupons=(lizard_coupon_sum / total) if lizard else None,
    )


# ---------------------------------------------------------------------------
# 녹아웃형
# ---------------------------------------------------------------------------


def price_knockout(ts: KnockoutTerms, market: MarketData, sim: SimSettings) -> PricingResult:
    """녹아웃형 평가. 최소 수익률 때문에 지급액이 참여율에 비선형일 수 있어
    경로별 (녹아웃 여부, 행사가 대비 수익, 손실액)을 모아 두고 참여율을 이분법으로 푼다.

    녹아웃 미발생 시 수익률 = max(최소 수익률, 참여율 × 수익) − min(불리한 움직임, 최대 손실률).
    불리한 움직임: 상승형은 행사가 대비 하락폭, 하락형은 상승폭, 양방향은 없음(0).
    """
    months = ts.maturity_months
    upper, lower = ts.ko_upper, ts.ko_lower
    daily = ts.ko_observation == "daily"
    direction = ts.direction

    ko_parts: list[np.ndarray] = []
    gain_parts: list[np.ndarray] = []
    loss_parts: list[np.ndarray] = []

    for n, months_iter in _iter_batches(market, months, sim):
        ko = np.zeros(n, dtype=bool)
        w_T = None
        for m, w_end, w_min, w_max in months_iter:
            if daily or m == months:
                hi = w_max if daily else w_end
                lo = w_min if daily else w_end
                if upper is not None:
                    ko |= hi >= upper
                if lower is not None:
                    ko |= lo <= lower
            w_T = w_end
        if direction == "up":
            gain = np.maximum(w_T - ts.strike, 0.0)
            adverse = np.maximum(ts.strike - w_T, 0.0)
        elif direction == "down":
            gain = np.maximum(ts.strike - w_T, 0.0)
            adverse = np.maximum(w_T - ts.strike, 0.0)
        else:
            gain = np.abs(w_T - ts.strike)
            adverse = np.zeros(n)
        ko_parts.append(ko)
        gain_parts.append(gain)
        cap = 1.0 if ts.max_loss is None else ts.max_loss  # 제한 없음이면 원금 전액까지
        loss_parts.append(np.where(ko, 0.0, np.minimum(adverse, cap)))  # 녹아웃이면 손실 없음

    df = float(np.exp(-market.rate * months / 12.0))

    def pv_paths(p: float) -> list[np.ndarray]:
        return [
            df * np.where(ko, 1.0 + ts.rebate, 1.0 + np.maximum(ts.min_return, p * g) - loss)
            for ko, g, loss in zip(ko_parts, gain_parts, loss_parts)
        ]

    def mean_pv(p: float) -> float:
        parts = pv_paths(p)
        return float(sum(x.sum() for x in parts) / sum(len(x) for x in parts))

    fair = _solve_participation(mean_pv, sim.target)
    p_used = ts.participation if ts.participation is not None else fair
    p_used = p_used if p_used is not None else 0.0

    stats = _PairStats()
    for x in pv_paths(p_used):
        stats.add(x, np.zeros_like(x))

    total = sum(len(k) for k in ko_parts)
    ko_all = np.concatenate(ko_parts)
    gain_all = np.concatenate(gain_parts)
    loss_all = np.concatenate(loss_parts)
    no_ko = ~ko_all
    lost = loss_all > 0
    ret_no_ko = np.maximum(ts.min_return, p_used * gain_all[no_ko]) - loss_all[no_ko]

    return PricingResult(
        product_type="knockout",
        price=mean_pv(ts.participation) if ts.participation is not None else None,
        stderr=stats.stderr(0.0),
        target=sim.target,
        fair_participation=fair,
        n_paths=total,
        ko_prob=float(ko_all.mean()),
        avg_return_no_ko=float(ret_no_ko.mean()) if no_ko.any() else None,
        min_return_prob=float((no_ko & ~lost & (p_used * gain_all <= ts.min_return)).mean()),
        maturity_loss_prob=float(lost.mean()),
        avg_loss_redemption=float((1.0 - loss_all[lost]).mean()) if lost.any() else None,
    )


def _solve_participation(mean_pv, target: float, p_max: float = 20.0) -> float | None:
    """mean_pv(p) = target인 참여율 p. 참여율 0에서 이미 목표 이상이면 0, p_max로도 못 미치면 None."""
    if mean_pv(0.0) >= target:
        return 0.0
    if mean_pv(p_max) < target:
        return None
    lo, hi = 0.0, p_max
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if mean_pv(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
