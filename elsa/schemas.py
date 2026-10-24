"""ELSA 데이터 모델 (Pydantic v2).

패키지 안의 모든 비율은 소수(0.85 = 85%)로 다룬다. 의미 검증(배리어 개수, 범위 등)은
validation.py가 한국어 메시지로 하고, 여기서는 타입과 구조만 정의한다.

- AutocallTerms: 자동조기상환형 조건 (일반형·월지급식·리자드, 최대 손실률로 원금지급형·부분 보장 표현)
- KnockoutTerms: 녹아웃형 조건 (상승형·하락형·양방향, 최대 손실률로 원금 보장 비율 표현)
- TermSheet: 위 두 조건을 product_type으로 구분하는 discriminated union
- MarketData: 변동성·배당·상관계수·금리
- SimSettings: 경로 수, 시드, 적정 쿠폰 목표값, 배치 크기
- PricingRequest / PricingResult: 평가 엔진 입력·출력
- ExtractionResult: 이미지 추출 결과 (추출기 종류와 무관하게 같은 형태)
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

KiMode = Literal["daily", "monthly", "maturity", "none"]  # 매일 / 매월 말일 / 만기일 / 노낙인
KoObservation = Literal["daily", "maturity"]
ProductType = Literal["autocall", "knockout"]


class AutocallTerms(BaseModel):
    """자동조기상환형 ELS 조건. monthly_coupon_barrier가 있으면 월지급식."""

    product_type: Literal["autocall"] = "autocall"
    underlyings: list[str]
    maturity_months: int
    obs_period_months: int
    barriers: list[float]
    coupon_annual: float | None = None
    ki_mode: KiMode = "daily"
    ki_barrier: float | None = None
    dummy_coupon: bool = False
    monthly_coupon_barrier: float | None = None
    max_loss: float | None = None  # 최대 손실률. 0 = 원금지급형(ELB), 0.1 = 원금 90% 보장, None = 제한 없음
    # 회차별 리자드 배리어. 90(L75)-90(L70)-85-80-75-70이면 [0.75, 0.70] (이후 회차는 리자드 없음).
    # 리스트가 평가 횟수보다 짧으면 나머지 회차는 None. 항목이 None인 회차는 리자드 없음. None이면 일반형
    lizard_barriers: list[float | None] | None = None

    @property
    def n_obs(self) -> int:
        """평가(조기상환 판정) 횟수."""
        return self.maturity_months // self.obs_period_months

    @property
    def is_monthly(self) -> bool:
        return self.monthly_coupon_barrier is not None

    def lizard_at(self, k: int) -> float | None:
        """k번째(0부터) 평가일의 리자드 배리어. 없으면 None."""
        if not self.lizard_barriers or k >= len(self.lizard_barriers):
            return None
        return self.lizard_barriers[k]

    @property
    def is_lizard(self) -> bool:
        return any(v is not None for v in (self.lizard_barriers or []))

    @property
    def principal_floor(self) -> float:
        """만기 최저 상환 비율 = 1 − 최대 손실률 (제한 없으면 0)."""
        return 0.0 if self.max_loss is None else 1.0 - self.max_loss


class KnockoutTerms(BaseModel):
    """녹아웃형 조건. ko_upper/ko_lower 중 하나 이상 필요.

    녹아웃 미발생 시 기초자산이 불리한 방향으로 움직인 만큼(상승형은 행사가 대비 하락폭, 하락형은 상승폭)
    손실을 보되 max_loss까지만 잃는다. max_loss = 0이면 원금지급형(ELB), None이면 제한 없음.
    원금 보장 여부를 가정하지 않는다(자동조기상환형과 같은 규칙).
    """

    product_type: Literal["knockout"] = "knockout"
    underlyings: list[str]
    maturity_months: int
    strike: float = 1.0
    participation: float | None = None
    ko_upper: float | None = None
    ko_lower: float | None = None
    rebate: float = 0.0
    ko_observation: KoObservation = "daily"
    min_return: float = 0.0
    max_loss: float | None = None  # 최대 손실률. 0 = 원금지급형(ELB), 0.1 = 원금 90% 보장, None = 제한 없음

    @property
    def direction(self) -> Literal["up", "down", "two_way"] | None:
        """상승형 / 하락형 / 양방향. 배리어가 없으면 None."""
        if self.ko_upper is not None and self.ko_lower is not None:
            return "two_way"
        if self.ko_upper is not None:
            return "up"
        if self.ko_lower is not None:
            return "down"
        return None


TermSheet = Annotated[Union[AutocallTerms, KnockoutTerms], Field(discriminator="product_type")]


class MarketData(BaseModel):
    """시장 데이터. 상품 안내 이미지에는 나오지 않으므로 사용자가 입력한다."""

    vols: list[float]
    divs: list[float]
    corr: list[list[float]]
    rate: float


class SimSettings(BaseModel):
    """시뮬레이션 설정. target은 적정 쿠폰을 구할 때의 목표 공정가치(액면 = 1.0)."""

    n_paths: int = 100_000
    seed: int = 42
    target: float = 1.0
    batch_size: int = 20_000


class PricingRequest(BaseModel):
    term_sheet: TermSheet
    market: MarketData
    sim: SimSettings = SimSettings()


class PricingResult(BaseModel):
    """평가 결과. 상품 유형에 해당하지 않는 필드는 None."""

    product_type: ProductType
    price: float | None  # 쿠폰(참여율)이 비어 있으면 None
    stderr: float
    target: float
    fair_coupon_annual: float | None = None
    fair_participation: float | None = None
    n_paths: int

    # 자동조기상환형
    obs_months: list[int] | None = None
    redemption_probs: list[float] | None = None  # 회차별 상환 확률 (마지막 = 만기 손실 없는 상환)
    early_redemption_prob: float | None = None
    maturity_loss_prob: float | None = None
    avg_loss_redemption: float | None = None  # 손실 시 평균 상환율 (손실 경로 없으면 None)
    expected_life_months: float | None = None
    ki_unredeemed_prob: float | None = None  # 낙인 발생 후 만기까지 미상환 확률
    avg_monthly_coupons: float | None = None  # 월지급식: 평균 월 쿠폰 수령 횟수
    avg_lizard_coupons: float | None = None  # 리자드: 평균 리자드 쿠폰 수령 횟수 (평가일 기준)

    # 녹아웃형
    ko_prob: float | None = None
    avg_return_no_ko: float | None = None  # 녹아웃 미발생 시 평균 수익률
    min_return_prob: float | None = None  # 최소 수익만 받을 확률


class ExtractionResult(BaseModel):
    """이미지 추출 결과. fields는 두 상품 유형의 필드를 모두 Optional로 담는 dict."""

    product_type: ProductType | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    ticker_suggestions: dict[str, str] = Field(default_factory=dict)
    field_confidence: dict[str, Literal["high", "low"]] = Field(default_factory=dict)
    source_text: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    unsupported_features: list[str] = Field(default_factory=list)
    extractor: str = ""
