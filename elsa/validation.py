"""조건 검증 (수동 입력·이미지 자동 입력 공통).

오류(errors)는 평가를 막고, 경고(warnings)는 평가는 하되 확인을 권한다.
메시지는 무엇을 어떻게 고치면 되는지 한국어로 쓴다. 비율은 화면 단위(%)로 보여 준다.

- ValidationReport: errors / warnings 목록과 ok 여부
- validate_terms(term_sheet): 상품 조건 검증 (자동조기상환형·녹아웃형)
- validate_market(market, n_underlyings): 변동성·배당·상관계수·금리 검증
- validate_sim(sim): 경로 수 등 시뮬레이션 설정 검증
- validate_request(request): 위 세 가지를 한 번에
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from elsa.schemas import AutocallTerms, KnockoutTerms, MarketData, PricingRequest, SimSettings

MIN_PATHS = 10_000
MAX_PATHS = 100_000
MAX_UNDERLYINGS = 3


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: ValidationReport) -> ValidationReport:
        self.errors += other.errors
        self.warnings += other.warnings
        return self


def _pct(x: float) -> str:
    return f"{x * 100:g}%"


def _check_underlyings(names: list[str], r: ValidationReport) -> None:
    if not 1 <= len(names) <= MAX_UNDERLYINGS:
        r.errors.append(f"기초자산은 1~{MAX_UNDERLYINGS}개까지 입력할 수 있습니다. 지금은 {len(names)}개입니다.")
    for i, name in enumerate(names, 1):
        if not name or not name.strip():
            r.errors.append(f"{i}번째 기초자산 이름이 비어 있습니다. 종목명을 입력해 주세요.")


def validate_terms(ts: AutocallTerms | KnockoutTerms) -> ValidationReport:
    r = ValidationReport()
    _check_underlyings(ts.underlyings, r)
    if ts.maturity_months <= 0:
        r.errors.append("만기(개월)는 0보다 커야 합니다.")
    if isinstance(ts, AutocallTerms):
        _check_autocall(ts, r)
    else:
        _check_knockout(ts, r)
    return r


def _check_autocall(ts: AutocallTerms, r: ValidationReport) -> None:
    if ts.obs_period_months <= 0:
        r.errors.append("조기상환 평가 주기(개월)는 0보다 커야 합니다.")
        return
    if ts.maturity_months > 0 and ts.maturity_months % ts.obs_period_months != 0:
        r.errors.append(
            f"만기 {ts.maturity_months}개월이 평가 주기 {ts.obs_period_months}개월로 나누어떨어지지 않습니다. "
            "만기나 평가 주기를 고쳐 주세요."
        )
        return
    n_obs = ts.n_obs
    if len(ts.barriers) != n_obs:
        r.errors.append(
            f"평가가 {n_obs}회이므로 상환 배리어를 {n_obs}개 입력해 주세요. 지금은 {len(ts.barriers)}개입니다."
        )
    for k, b in enumerate(ts.barriers, 1):
        if b <= 0:
            r.errors.append(f"{k}회차 상환 배리어는 0%보다 커야 합니다.")
    if ts.coupon_annual is not None and ts.coupon_annual < 0:
        r.errors.append("쿠폰은 0% 이상이어야 합니다.")
    if ts.ki_mode != "none":
        if ts.ki_barrier is None or ts.ki_barrier <= 0:
            r.errors.append("낙인 배리어를 0%보다 큰 값으로 입력하거나, 낙인 방식을 '노낙인'으로 바꿔 주세요.")
        elif ts.barriers and ts.ki_barrier >= ts.barriers[-1]:
            r.warnings.append(
                f"낙인 배리어({_pct(ts.ki_barrier)})가 만기 상환 배리어({_pct(ts.barriers[-1])}) 이상입니다. "
                "값이 맞는지 확인해 주세요."
            )
    if ts.monthly_coupon_barrier is not None and not 0 < ts.monthly_coupon_barrier <= 1.5:
        r.errors.append("월지급 배리어는 0%보다 크고 150% 이하여야 합니다.")
    if ts.max_loss is not None and not 0 <= ts.max_loss <= 1:
        r.errors.append("최대 손실률은 0%(원금지급형)~100% 사이로 입력하거나, 제한이 없으면 비워 주세요.")
    if ts.lizard_barriers is not None:
        if len(ts.lizard_barriers) > ts.n_obs:
            r.errors.append(
                f"평가가 {ts.n_obs}회이므로 리자드 배리어는 최대 {ts.n_obs}개까지 입력할 수 있습니다. "
                f"지금은 {len(ts.lizard_barriers)}개입니다."
            )
        if any(v is not None and not 0 < v <= 1.5 for v in ts.lizard_barriers):
            r.errors.append("리자드 배리어는 0%보다 크고 150% 이하여야 합니다.")
        if ts.is_lizard and ts.is_monthly:
            r.errors.append("리자드와 월지급식은 함께 쓸 수 없습니다. 둘 중 하나만 선택해 주세요.")
        if r.ok and len(ts.barriers) == ts.n_obs:
            same = [k + 1 for k in range(ts.n_obs)
                    if ts.lizard_at(k) is not None and ts.lizard_at(k) >= ts.barriers[k]]
            if same:
                r.warnings.append(
                    f"{', '.join(map(str, same))}회차의 리자드 배리어가 상환 배리어 이상이라 그 회차에는 리자드 효과가 없습니다. "
                    "값이 맞는지 확인해 주세요."
                )


def _check_knockout(ts: KnockoutTerms, r: ValidationReport) -> None:
    if ts.ko_upper is None and ts.ko_lower is None:
        r.errors.append("녹아웃 배리어를 상승·하락 중 하나 이상 입력해 주세요.")
    if ts.strike <= 0:
        r.errors.append("행사가는 0%보다 커야 합니다.")
    if ts.ko_upper is not None and ts.ko_upper <= ts.strike:
        r.errors.append(
            f"상승 녹아웃 배리어({_pct(ts.ko_upper)})는 행사가({_pct(ts.strike)})보다 커야 합니다."
        )
    if ts.ko_lower is not None and ts.ko_lower >= ts.strike:
        r.errors.append(
            f"하락 녹아웃 배리어({_pct(ts.ko_lower)})는 행사가({_pct(ts.strike)})보다 작아야 합니다."
        )
    if ts.participation is not None and ts.participation < 0:
        r.errors.append("참여율은 0% 이상이어야 합니다.")
    if ts.rebate < 0:
        r.errors.append("녹아웃 발생 시 수익률은 0% 이상이어야 합니다.")
    if ts.min_return < 0:
        r.errors.append("최소 수익률은 0% 이상이어야 합니다.")
    if ts.max_loss is not None and not 0 <= ts.max_loss <= 1:
        r.errors.append("최대 손실률은 0%(원금지급형)~100% 사이로 입력하거나, 제한이 없으면 비워 주세요.")
    elif ts.min_return > 0 and ts.max_loss != 0:
        r.errors.append(
            "최소 수익률이 있으면 원금 손실이 없는 상품이므로 최대 손실률을 0%로 입력하거나, 최소 수익률을 0%로 두세요."
        )


def validate_market(market: MarketData, n_underlyings: int) -> ValidationReport:
    r = ValidationReport()
    n = n_underlyings
    if len(market.vols) != n or len(market.divs) != n:
        r.errors.append(f"기초자산이 {n}개이므로 변동성과 배당수익률을 {n}개씩 입력해 주세요.")
        return r
    for i, v in enumerate(market.vols, 1):
        if v <= 0:
            r.errors.append(f"{i}번째 기초자산의 변동성은 0%보다 커야 합니다.")
        elif v > 1.5:
            r.warnings.append(f"{i}번째 기초자산의 변동성({_pct(v)})이 매우 큽니다. 단위를 확인해 주세요.")
    corr = np.asarray(market.corr, dtype=float)
    if corr.shape != (n, n):
        r.errors.append(f"상관계수 행렬은 {n}×{n}이어야 합니다.")
        return r
    if not np.allclose(corr, corr.T):
        r.errors.append("상관계수 행렬이 대칭이 아닙니다. (i, j)와 (j, i) 값을 같게 입력해 주세요.")
    if not np.allclose(np.diag(corr), 1.0):
        r.errors.append("상관계수 행렬의 대각선(자기 자신과의 상관)은 1이어야 합니다.")
    off = corr[~np.eye(n, dtype=bool)]
    if off.size and (off.min() < -1 or off.max() > 1):
        r.errors.append("상관계수는 -1과 1 사이여야 합니다.")
    if r.ok:
        try:
            np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            r.errors.append(
                "상관계수 조합이 서로 모순됩니다(양의 정부호가 아님). 상관계수를 조금씩 낮춰 다시 시도해 주세요."
            )
    if not -0.05 <= market.rate <= 0.2:
        r.warnings.append(f"무위험이자율({_pct(market.rate)})이 일반적인 범위를 벗어납니다. 단위를 확인해 주세요.")
    return r


def validate_sim(sim: SimSettings) -> ValidationReport:
    r = ValidationReport()
    if not MIN_PATHS <= sim.n_paths <= MAX_PATHS:
        r.errors.append(f"경로 수는 {MIN_PATHS:,}~{MAX_PATHS:,} 사이로 입력해 주세요.")
    if sim.target <= 0:
        r.errors.append("적정 쿠폰 목표값은 0%보다 커야 합니다.")
    return r


def validate_request(req: PricingRequest) -> ValidationReport:
    r = validate_terms(req.term_sheet)
    r.extend(validate_market(req.market, len(req.term_sheet.underlyings)))
    return r.extend(validate_sim(req.sim))
