"""민감도 분석: 시장 가정을 바꿨을 때 공정가치·적정 쿠폰이 얼마나 변하는지 (공통 난수).

모든 시나리오는 같은 시드로 평가해 시나리오 간 차이가 난수 차이가 아니라 가정 차이만 반영되게 한다.

- SCENARIOS: 기준, 상관계수 ±0.1, 변동성 ±5%p(모든 자산), 금리 ±0.5%p
- ScenarioResult: 시나리오 이름, 공정가치, 적정 쿠폰(또는 참여율), 건너뛴 이유
- shift_market(market, d_corr, d_vol, d_rate): 시장 데이터를 시나리오만큼 이동
- run_sensitivity(request): 모든 시나리오 평가
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from elsa.pricing.engine import price
from elsa.schemas import MarketData, PricingRequest

SCENARIOS: list[tuple[str, float, float, float]] = [
    ("기준", 0.0, 0.0, 0.0),
    ("상관계수 +0.1", 0.1, 0.0, 0.0),
    ("상관계수 −0.1", -0.1, 0.0, 0.0),
    ("변동성 +5%p", 0.0, 0.05, 0.0),
    ("변동성 −5%p", 0.0, -0.05, 0.0),
    ("금리 +0.5%p", 0.0, 0.0, 0.005),
    ("금리 −0.5%p", 0.0, 0.0, -0.005),
]


@dataclass
class ScenarioResult:
    label: str
    price: float | None
    fair: float | None
    skipped: str | None = None


def shift_market(mk: MarketData, d_corr: float, d_vol: float, d_rate: float) -> MarketData:
    corr = np.asarray(mk.corr, dtype=float)
    off = ~np.eye(len(corr), dtype=bool)
    corr[off] = np.clip(corr[off] + d_corr, -0.99, 0.99)
    return MarketData(
        vols=[max(v + d_vol, 0.01) for v in mk.vols],
        divs=list(mk.divs),
        corr=corr.tolist(),
        rate=mk.rate + d_rate,
    )


def run_sensitivity(req: PricingRequest) -> list[ScenarioResult]:
    n_assets = len(req.term_sheet.underlyings)
    out = []
    for label, d_corr, d_vol, d_rate in SCENARIOS:
        if d_corr and n_assets < 2:
            continue
        mk = shift_market(req.market, d_corr, d_vol, d_rate)
        try:
            np.linalg.cholesky(np.asarray(mk.corr))
        except np.linalg.LinAlgError:
            out.append(ScenarioResult(label, None, None, "상관계수 조합이 유효하지 않아 건너뜀"))
            continue
        r = price(req.model_copy(update={"market": mk}))
        fair = r.fair_coupon_annual if r.product_type == "autocall" else r.fair_participation
        out.append(ScenarioResult(label, r.price, fair))
    return out
