# CLAUDE.md

이 파일은 이 저장소에서 작업하는 Claude Code가 읽는 프로젝트 지침입니다.
작업 전에 끝까지 읽고, 여기 적힌 결정과 규칙을 따르세요. 결정을 바꿔야 할 이유가 있으면 코드를 고치기 전에 먼저 사용자에게 제안하세요.

---

## 1. 프로젝트 개요

**ELSA (ELS Scan & Assess).** ELS의 조건을 입력하거나 상품 안내 이미지를 올리면, 몬테카를로 시뮬레이션으로 공정가치·적정 쿠폰·회차별 상환 확률을 계산하는 Streamlit 웹 앱이다.

GitHub(`018lim/elsa`)에 공개하고 Streamlit Community Cloud에 배포해, 채용 담당자가 링크로 바로 써 볼 수 있게 하는 포트폴리오 프로젝트다. 처음 여는 사람도 1분 안에 기능을 이해할 수 있어야 한다.

### 지원 상품 (`product_type`)
- `autocall` **자동조기상환형**: 회차별 배리어를 자유롭게 입력하므로 스텝다운·평평한 배리어·노낙인·만기 낙인을 같은 엔진으로 계산한다. 변형으로 **월지급식**, **리자드(슈퍼리자드 포함)**. UI·문서에서는 "스텝다운 ELS"가 아니라 "자동조기상환형 ELS"로 부른다
- `knockout` **녹아웃형**: 상승형·하락형·양방향, 매일/만기 관찰
- 두 유형 모두 원금 보장 여부를 가정하지 않고 **최대 손실률**로 입력받는다(0% = 원금지급형 ELB, 비움 = 제한 없음)

### 입력 방법 (둘 다 항상 지원)
1. **수동 입력**: 폼에 조건을 직접 입력
2. **이미지 자동 입력**: 상품 안내 이미지(간이투자설명서, 상품 요약표, 증권사 앱 캡처)를 올리면 조건을 읽어 폼에 채움 → 사용자가 확인·수정 → 평가

자동 입력은 **폼을 채우기만 하고 평가를 실행하지 않는다.**

쿠폰은 상품 안내 이미지에 없는 경우가 많다. 쿠폰이 비어 있어도 평가할 수 있어야 하며, 결과에 항상 **적정 쿠폰**(공정가치가 목표값이 되는 연 쿠폰, 녹아웃은 적정 참여율)을 보여 준다.

### 테스트용 실제 캡처
`tests/fixtures/images/`에 실제 증권사 상품 안내 캡처가 있다(.gitignore 대상, 로컬 전용, `pytest -m live`에서만 사용). 저장소·샘플·외부 공개에 쓰지 않는다. 계좌 화면처럼 개인정보가 담긴 캡처는 읽지도 보내지도 않는다.

---

## 2. 기술 스택 (결정 사항)

| 영역 | 선택 | 이유·비고 |
|---|---|---|
| 앱 | Python 3.11, Streamlit | 파이썬 하나로 화면까지 구현, Community Cloud 무료 배포 |
| 평가 엔진 | numpy | 10만 경로 약 3초 |
| 데이터 검증 | Pydantic v2 | 조건·시장 데이터·평가 결과·추출 결과 스키마 |
| 이미지 분석 | 교체 가능한 추출기. 기본 Gemini(`google-genai`), 대안 Claude(`anthropic`) | 모델은 설정값으로만 지정. 권장 `gemini-flash-lite-latest`(장당 약 10초, 실측 정확도 충분). `gemini-flash-latest`는 80~250초로 느렸음 |
| 시세 | yfinance (+pandas) | 과거 종가로 실현변동성·상관계수·배당 추정. 비공식 라이브러리라 실패할 수 있으므로 항상 폴백(기본값·붙여넣기·직접 입력)을 둔다 |
| 금리 | 한국은행 ECOS API, 실패하면 네이버 금융 (표준 라이브러리 urllib) | 무위험이자율 = 국고채(3년). 원화 정산 상품이라 미국 국채 금리는 쓰지 않는다. pykrx는 KRX 계정 로그인이 필요하고 pandas 3과 충돌해 쓰지 않는다. Yahoo에는 금리가 없고(국채 ETF 가격뿐), FRED에는 3년물이 없어 쓰지 않는다 |
| 차트 | altair | 회차별 상환 확률 막대 차트 |
| PDF | pypdf | 업로드 PDF 페이지 수 확인 |
| 비밀값 | `st.secrets` (배포) / `.streamlit/secrets.toml` (로컬, 커밋 금지) | API 키를 저장소와 브라우저에 노출하지 않음 |
| 테스트 | pytest, streamlit `AppTest` | 엔진·정규화·검증 단위 테스트, 화면 흐름 테스트 |

- 파이썬은 가상환경 `.venv`(Python 3.11)를 쓴다. 실행 예: `.\.venv\Scripts\python.exe -m pytest`
- 변동성은 **과거(실현) 변동성**만 쓴다. 내재변동성은 쓰지 않는다.
- **API 키는 절대 코드나 저장소에 넣지 않는다.** `.streamlit/secrets.toml`은 `.gitignore`에 있고 `secrets.toml.example`만 커밋한다. 키가 든 파일은 열어 보지 않고, 바꿔야 하면 해당 줄만 바꾼다.
- 별도 백엔드 서버(FastAPI 등), 프론트엔드 프레임워크, DB는 도입하지 않는다. 필요해 보이면 먼저 제안한다.

---

## 3. 디렉터리 구조

```
ELSA/
├── CLAUDE.md
├── README.md
├── app.py                      # Streamlit 진입점. 화면 구성만, 계산 로직 없음
├── requirements.txt            # 배포용 (버전 고정)
├── requirements-dev.txt        # + pytest
├── pytest.ini                  # 기본 실행에서 live 테스트 제외
├── .gitignore
├── .streamlit/
│   ├── config.toml             # 업로드 최대 크기(10MB)
│   └── secrets.toml.example
├── scripts/
│   └── make_sample.py          # 체험용 가상 샘플 이미지 생성 (Pillow, Windows 맑은 고딕)
├── elsa/                       # 계산·추출 로직 패키지. streamlit을 import하지 않는다
│   ├── config.py               # 설정 읽기: st.secrets → 환경변수 순 (streamlit import는 함수 안에서만)
│   ├── schemas.py              # AutocallTerms, KnockoutTerms, MarketData, SimSettings, PricingRequest/Result, ExtractionResult
│   ├── validation.py           # 조건 검증 (수동·자동 공통)
│   ├── market.py               # 종목표 조회, yfinance 시세, 종가로 변동성·상관계수·배당 추정
│   ├── rates.py                # 국고채(3년) 금리 조회 (ECOS → 네이버 금융)
│   ├── data/underlyings.json   # 종목표 (이름·별칭·티커·기본 변동성·배당·대체 ETF). 코드 수정 없이 항목 추가
│   ├── pricing/
│   │   ├── engine.py           # 몬테카를로 엔진
│   │   ├── sensitivity.py      # 민감도 분석 (공통 난수)
│   │   ├── verification.py     # 엔진 검증: 같은 엔진으로 유럽형 콜옵션 계산, Black-Scholes와 비교
│   │   └── logic_checks.py     # ELS 로직 점검: 극단 입력에서 price() 결과를 손 계산값·방향과 비교
│   └── extraction/
│       ├── base.py             # Extractor 인터페이스, 오류 타입, 업로드 검사
│       ├── prompt.py           # 공통 프롬프트, 공통 JSON 스키마
│       ├── gemini.py           # GeminiExtractor (response_json_schema 구조화 출력)
│       ├── claude.py           # ClaudeExtractor (output_config.format 구조화 출력)
│       ├── factory.py          # 설정값 EXTRACTOR로 구현체 선택
│       └── normalize.py        # 원문 표기 → 조건 (순수 함수)
├── ui/                         # Streamlit 화면 조각
│   ├── state.py                # session_state 키, 위젯 값 고정, 자동 입력 반영, 화면 값 → 평가 요청
│   ├── form.py                 # 상품 조건·시장 데이터·시뮬레이션 설정 폼
│   ├── upload.py               # 이미지 업로드, 샘플 체험, 추출 결과 안내
│   ├── results.py              # 결과 지표, 회차별 상환 표·차트, 민감도
│   └── verification.py         # 엔진 검증 탭 (입력·표시만)
├── samples/                    # 체험용 가상 상품 이미지 (예시증권 제1호·제2호)
└── tests/
    ├── test_engine.py, test_validation.py, test_market.py, test_rates.py, test_sensitivity.py, test_verification.py, test_logic_checks.py
    ├── test_normalize.py, test_extraction_parse.py
    ├── test_app.py             # 화면 흐름 (streamlit AppTest)
    └── fixtures/               # 추출기 응답 JSON 예시와 기대 결과
        └── images/             # 실제 캡처 (로컬 전용, .gitignore)
```

---

## 4. 도메인 용어

코드·변수명·UI 문구에서 아래 용어를 일관되게 쓴다.

| 용어 | 의미 | 코드 이름 |
|---|---|---|
| 자동조기상환형 ELS | 정해진 평가일에 조건을 채우면 조기상환되는 ELS. 스텝다운은 그 한 종류 | `autocall` |
| 기초자산 | ELS 수익이 연동되는 종목·지수 | `underlyings` |
| 최초기준가격 | 발행일 기준가격. 모든 배리어는 이 가격 대비 % | (정규화 기준 1.0) |
| 워스트 퍼포머 | 기초자산 중 최초기준가격 대비 비율이 가장 낮은 것 | `worst` |
| 조기상환 배리어 | 평가일에 워스트가 이 비율 이상이면 조기상환 | `barriers` |
| 쿠폰 | 연 수익률. 회차별 지급액 = 연 쿠폰 × 경과 기간 | `coupon_annual` |
| 낙인(KI) | 워스트가 이 비율 아래로 떨어진 적이 있으면, 만기에 배리어 미달일 때 원금을 지켜 주던 보호막이 사라짐 | `ki_barrier`, `ki_mode` |
| 노낙인 | 낙인 판정이 없는 구조. 만기 배리어 미달이면 바로 워스트 비율만큼 상환 | `ki_mode="none"` |
| 더미 쿠폰 | 낙인 미발생 시 만기 배리어 미달이어도 지급하는 쿠폰 | `dummy_coupon` |
| 월지급식 | 매월 워스트가 월지급 배리어 이상이면 그달 쿠폰(연 쿠폰/12)을 바로 지급 | `monthly_coupon_barrier` |
| 리자드 | 평가일에 상환 배리어에는 못 미쳐도 리자드 배리어 이상이면 그 회차 쿠폰 지급. `90(L75)` 표기 | `lizard_barriers` |
| 최대 손실률 | 만기에 잃을 수 있는 최대 비율. 0 = 원금지급형(ELB), None = 제한 없음 | `max_loss` |
| ELB | 원금지급형 파생결합사채. 최대 손실률 0 | |
| 녹아웃 | 기간 중(또는 만기에) 워스트가 녹아웃 배리어에 닿으면 고정 수익으로 확정 | `ko_upper`, `ko_lower` |
| 리베이트 | 녹아웃 발생 시 받는 고정 수익 | `rebate` |
| 행사가·참여율 | 녹아웃 미발생 시 수익 = (워스트 − 행사가) × 참여율 (상승형) | `strike`, `participation` |
| 공정가치 | 위험중립 기대 상환금액의 현재가치, 액면 대비 % | `price` |
| 적정 쿠폰 | 공정가치가 목표값(기본 100%)이 되는 연 쿠폰. 녹아웃은 적정 참여율 | `fair_coupon_annual`, `fair_participation` |

---

## 5. 데이터 모델 (`elsa/schemas.py`)

### 5.1 TermSheet (상품 조건)

`product_type`으로 구분하는 Pydantic discriminated union: `TermSheet = AutocallTerms | KnockoutTerms`.

**AutocallTerms** (`product_type="autocall"`)
```
underlyings: list[str]                 # 1~3개
maturity_months: int                   # 예: 36
obs_period_months: int                 # 예: 6
barriers: list[float]                  # 소수. 길이 = maturity / period
coupon_annual: float | None            # 없으면 적정 쿠폰만 계산
ki_mode: "daily" | "monthly" | "maturity" | "none"   # 매일 / 매월 말일 / 만기일 / 노낙인
ki_barrier: float | None               # ki_mode="none"이면 None
dummy_coupon: bool
monthly_coupon_barrier: float | None   # 월지급식이면 값(예: 0.65)
max_loss: float | None                 # 0 = ELB, 0.1 = 원금 90% 보장, None = 제한 없음
lizard_barriers: list[float | None] | None   # 회차별. 90(L75)-90(L70)-85-80-75-70 → [0.75, 0.70]. 월지급식과 함께 쓸 수 없음
```

**KnockoutTerms** (`product_type="knockout"`)
```
underlyings: list[str]                 # 1~3개 (워스트 기준)
maturity_months: int                   # 예: 12
strike: float                          # 기본 1.0
participation: float | None            # 없으면 적정 참여율만 계산
ko_upper: float | None                 # 상승 녹아웃 배리어 (예: 1.37)
ko_lower: float | None                 # 하락 녹아웃 배리어. 둘 중 하나 이상, 둘 다면 양방향
rebate: float                          # 녹아웃 시 수익률
ko_observation: "daily" | "maturity"
min_return: float                      # 최소 보장 수익률 (0보다 크면 max_loss는 0이어야 함)
max_loss: float | None                 # 자동조기상환형과 같은 의미
```

### 5.2 MarketData
`vols`, `divs`, `corr`, `rate` (모두 소수). **상품 안내 이미지에는 나오지 않으므로 추출기가 만들어 내면 안 된다.** 사용자가 입력하거나 종목표 기본값·시세 추정·금리 불러오기를 쓴다.

### 5.3 ExtractionResult (추출기 종류와 무관하게 같은 형태)
```
product_type: "autocall" | "knockout" | None
fields: dict                          # 위 두 모델의 필드 중 읽은 것만
ticker_suggestions: dict[str, str]    # 종목표에 없는 종목의 AI 티커 제안 (low 확신도)
field_confidence: dict[str, "high" | "low"]
source_text: dict[str, str]           # 필드별 근거 원문 (위젯 help 툴팁에 표시)
warnings: list[str]
unsupported_features: list[str]
extractor: str                        # 예: "gemini:gemini-flash-lite-latest"
```

### 5.4 단위 규칙
- `elsa/` 패키지 내부는 모두 **소수**(0.85). 화면 입력·표시는 **%**(85). 변환은 `ui/` 경계에서 한 번만 한다.

---

## 6. 평가 엔진 (`elsa/pricing/engine.py`)

streamlit에 의존하지 않는 순수 함수. 공통 사항:
- 위험중립 기하브라운운동, 드리프트 `(r − q − σ²/2)`, 상관계수 행렬의 촐레스키 분해
- 연 252영업일, **월 21영업일**. k번째 평가일(0부터) = `(k+1) × obs_period_months × 21`번째 스텝
- 대조변수(antithetic). 표준오차는 쌍 평균 기준
- 경로를 배치(2만 개)로 나누고, 경로 전체를 저장하지 않고 한 달씩 생성해 워스트의 월말·월중 최저·최고만 넘긴다(메모리 절약). 난수·증분은 float32
- 시드로 재현. 민감도 분석은 같은 시드(공통 난수)

### 자동조기상환형 상환 판정
1. 마지막 회차 전 평가일: 워스트 ≥ `barriers[k]` → `1 + 회차별쿠폰 × (k+1)` 상환 (회차별쿠폰 = `coupon_annual × period / 12`)
2. 만기:
   - 워스트 ≥ `barriers[-1]` → `1 + 회차별쿠폰 × 회차수`
   - 아니고 낙인 미발생(`ki_mode != "none"`) → 더미 쿠폰이면 위와 동일, 아니면 원금(1)
   - 그 외(낙인 발생, 노낙인에서 배리어 미달) → `max(워스트, 1 − max_loss)` 상환. 상환액 < 1일 때만 '만기 손실'로 집계
3. 낙인: `daily` 매 스텝, `monthly` 매월 말일(만기 포함), `maturity` 만기일만, `none` 없음(더미 쿠폰 무시)
4. 조기상환된 경로에서 그 전에 발생한 낙인은 통계에 넣지 않는다

### 월지급식
매월 말 아직 상환되지 않았고 워스트 ≥ 월지급 배리어이면 `coupon_annual / 12` 지급. 조기상환·만기에는 원금만(누적 쿠폰 없음). 더미 쿠폰 무시.

### 리자드
- 평가일마다 아직 상환되지 않았고 워스트 ≥ 그 회차 기준이면 **그 회차 쿠폰**(연 쿠폰 × period / 12)을 그날 지급. 기준 = 리자드가 있는 회차는 `min(L, 상환 배리어)`, 없는 회차는 상환 배리어
- 상환 시 원금 + 그 회차 쿠폰만(누적 쿠폰 없음). 더미 쿠폰 무시. 만기 손실·낙인 규칙은 일반형과 같음
- 이 지급 규칙은 사용자와 정한 가정이다(상품마다 다를 수 있음). 화면 하단 가정에 명시돼 있다

### 녹아웃형
- 녹아웃: (`ko_upper` 있고 워스트 ≥ `ko_upper`) 또는 (`ko_lower` 있고 워스트 ≤ `ko_lower`). `daily`는 매 스텝, `maturity`는 만기만
- 녹아웃 → `1 + rebate`
- 미발생 → `1 + max(min_return, participation × 수익) − min(불리한 움직임, max_loss)`
  - 수익: 상승형 `max(P−K, 0)`, 하락형 `max(K−P, 0)`, 양방향 `|P−K|`
  - 불리한 움직임: 상승형 `max(K−P, 0)`, 하락형 `max(P−K, 0)`, 양방향 0. `max_loss`가 None이면 한도 1
- 모두 만기 지급, 만기 할인. 원금 보장을 가정하지 않는다

### 적정 쿠폰 / 적정 참여율
자동조기상환형은 지급액이 쿠폰에 선형이고 상환 판정은 쿠폰과 무관하므로, 한 번의 시뮬레이션에서 `A`(쿠폰 무관 현재가치)와 `B`(쿠폰 1.0당 현재가치)를 모아 `fair = (target − A) / B`. 녹아웃은 최소 수익률 때문에 비선형일 수 있어 경로별 값을 저장해 두고 이분법으로 푼다. `target` 기본 1.0.

### 출력 (`PricingResult`)
- 공통: `price`(쿠폰·참여율이 없으면 None), `stderr`, `target`, `fair_coupon_annual` / `fair_participation`, `n_paths`
- 자동조기상환형: 회차별 상환 확률, 조기상환 확률 합계, 만기 손실 확률, 손실 시 평균 상환율, 기대 보유기간, 낙인 후 미상환 확률, 월 쿠폰·리자드 쿠폰 평균 수령 횟수
- 녹아웃형: 녹아웃 확률, 미발생 시 평균 수익률, 최소 수익만 받을 확률, 만기 손실 확률, 손실 시 평균 상환율

### 기준값 (회귀 테스트, 엔진을 고치면 반드시 확인)
삼성전자/SK하이닉스, 변동성 32%/45%, 배당 2.0%/0.8%, 상관 0.6, 금리 2.8%, 36개월/6개월, 90-90-85-85-80-75, 쿠폰 8%, KI 50% 매일, 더미 쿠폰, 10만 경로, 시드 42
→ **공정가치 약 85.7%** (±0.5%p), 6개월 상환 약 47%, 만기 손실 약 27%

### 엔진 검증 (`elsa/pricing/verification.py`, 탭 `ui/verification.py`)
해석해가 있는 유럽형 콜옵션(단일 기초자산)으로 엔진을 확인한다.
- 새 시뮬레이터를 만들지 않는다. `_iter_batches`, `discount_factor`, `_PairStats`를 그대로 쓰고 페이오프만 `max(S_T − K, 0)`로 바꾼다
- 정답은 배당 포함 Black-Scholes. N(·)는 `math.erfc`(scipy를 넣지 않는다). 만기는 엔진 격자에 맞춰 가장 가까운 개월로 맞추고 MC·BS에 같은 값을 쓴다
- 판정 표준오차는 **쌍 평균 기준**이다. 경로별 표준편차 / √N은 대조변수 쌍이 독립이 아니라 표준오차를 크게 잡으므로 판정에 쓰지 않고, 대조변수 효과 비교에만 쓴다
- 일괄 검증의 행사가는 현재가의 80~120%. 같은 시드·변동성이면 경로가 같아 변동성별로 시뮬레이션을 한 번만 한다
- 경로 생성·할인만 확인된다. 낙인·조기상환 판정, 월중 최저·최고, 상관계수·워스트 계산은 이 검증으로 확인되지 않는다

### ELS 로직 점검 (`elsa/pricing/logic_checks.py`)
콜옵션으로 확인할 수 없는 판정 로직을 극단 입력으로 점검한다. 기존 `price()`만 호출하고 엔진은 바꾸지 않는다. 기대값은 엔진 코드를 쓰지 않고 공식으로 따로 계산한다.
- 손 계산값 비교 (변동성 ≈ 0, 1회차 무조건 상환): `|차이| ≤ max(1.96 × 표준오차, 0.0001%p)`. 표준오차가 사실상 0이라 신뢰구간만 쓰면 float32 반올림 오차(10⁻⁹ 수준)가 ⚠️로 잡힌다
- 차이 0 비교 (상관계수 ≈ 1): 자산 수가 다르면 같은 시드여도 난수 배치가 달라 공통 난수가 아니므로 `1.96 × √(se₁² + se₂²)`로 판정한다. 95% 기준이라 시드에 따라 가끔 ⚠️가 날 수 있고, 화면에 ※로 표시한다
- 방향 비교: 같은 시드(공통 난수)로 조건만 바꿔 대소만 본다
- 낙인 제거는 `ki_mode="none"`을 쓰면 안 된다(노낙인은 보호 자체를 없애 가격이 내려간다). 낙인 배리어를 0.01%로 낮춘다
- 상관계수는 0.999999999를 쓴다. 1.0은 촐레스키 분해가 실패하고, 0.99는 워스트 효과가 남는다(화면 입력 상한은 0.99 그대로)
- 일반형의 변동성 방향은 σ 20%부터 본다. 그보다 낮으면 상환이 늦어져 쿠폰을 더 받는 효과로 가격이 오른다. 녹아웃형은 상승 후 하락이 정상이다
- 상관계수 방향은 일반형만 본다. 녹아웃형은 수익도 녹아웃도 함께 줄어 이론상 방향이 정해지지 않는다
- 경로 수는 판정 종류별로 고정(손 계산 1만, 차이 0 10만, 방향 5만)이고 시드만 탭 입력값을 쓴다

---

## 7. 이미지 자동 입력

### 7.1 추출기
- `Extractor.extract(file_bytes, mime_type) -> ExtractionResult`. 구현체 `GeminiExtractor`(기본), `ClaudeExtractor`
- `factory.get_extractor()`가 설정값 `EXTRACTOR`, `EXTRACTOR_MODEL`로 만든다. 모델 이름은 코드에 넣지 않는다
- 두 구현체는 `prompt.py`의 같은 프롬프트·JSON 스키마를 쓴다. Gemini는 `response_json_schema`, Claude는 `output_config.format`. 현재 Claude 모델은 tool 강제 호출을 400으로 거부하므로 쓰지 않는다
- 추출기는 값을 해석하지 않고 **원문 표기를 필드별 문자열로** 옮긴다(`barriers_text`, `coupon_text`, `max_loss_text`, `product_name_text` 등). 단위 변환·날짜 계산·배리어 분해는 모두 `normalize.py`가 한다
- 오류 변환: 429 → `QuotaExceeded`, 키·모델 없음·잘못된 키 → `NotConfigured`, 파싱 실패·타임아웃 → `ExtractionFailed`, 파일 형식·크기·PDF 페이지 → `UploadRejected`. Gemini 5xx는 최대 2회 재시도

### 7.2 화면 흐름 (`ui/upload.py`)
1. 업로드(PNG·JPG·WEBP·PDF 3페이지 이하, 10MB) + "조건 읽기". 그 아래에 "체험할 샘플" 선택 + "샘플 이미지로 체험"
2. 버튼 콜백은 작업(`extract_job`)만 등록한다. 다음 실행에서 업로드 영역 대신 "읽는 중" 안내만 그린 채 추출한다(판독 중 다른 버튼을 눌러 작업이 끊기지 않도록)
3. 결과를 `pending_fill`에 넣고 rerun → 폼에 반영(7.3)
4. 안내: 읽은 항목 수, 확인 필요(low) 항목, 찾지 못한 항목(최대 손실률 포함), 쿠폰·참여율이 없을 때 적정값 안내, 경고·미지원 구조
5. 샘플 추출 결과는 `st.cache_data`로 서버에 캐시해 API 사용량을 아낀다

오류 문구 (폼은 그대로 쓸 수 있어야 함)
- `QuotaExceeded`: "오늘 자동 입력 한도를 모두 사용했습니다. 조건을 직접 입력해 주세요."
- `NotConfigured`: "이미지 자동 입력이 설정되지 않았습니다. 조건을 직접 입력해 주세요."
- `ExtractionFailed`: "이미지에서 조건을 읽지 못했습니다. 더 선명한 이미지를 올리거나 직접 입력해 주세요."

업로드 영역 아래: "업로드한 이미지는 외부 AI 서비스로 전송됩니다. 계좌번호 등 개인정보가 담긴 화면은 올리지 마세요."

### 7.3 Streamlit 상태 패턴 (중요, `ui/state.py`)
1. 모든 폼 위젯은 `ui/state.py`의 고정 key를 쓴다(예: `ac_maturity`, `ac_barriers`, `ko_max_loss`, `ul_name_0`)
2. 위젯이 만들어진 뒤에는 같은 실행에서 값을 바꿀 수 없으므로, 추출 결과는 `pending_fill`에 넣고 `st.rerun()` → 다음 실행 맨 앞에서 `apply_pending_fill()`이 위젯 key에 옮겨 쓴다
3. Streamlit은 화면에 그려지지 않은 위젯 값을 지운다. 그래서 모든 위젯 key를 `_widget_defaults()`에 넣고 `persist_widgets()`가 매 실행 시작 시 고정한다. **새 위젯을 만들면 반드시 여기에 기본값을 추가한다**
4. 자동 입력한 필드와 근거 원문은 `autofill_meta`에 보관해 라벨 "(이미지)"와 help 툴팁에 쓴다
5. 이미지에 없는 값은 예시값이 남지 않도록 비운다(`_CLEAR_IF_MISSING`). 최대 손실률도 비운다(원금 보장을 가정하지 않음)
6. 배리어 칸에 `90(L75)-80`처럼 리자드를 괄호로 적으면 리자드 체크박스보다 우선한다

### 7.4 프롬프트 원칙 (`prompt.py`)
- 이미지에 없는 값은 반드시 null. 시장 데이터는 추출하지 않는다
- 상품 유형 판별, 필드별 근거 원문과 확신도
- 예외 하나: 종목표에 없는 기초자산의 Yahoo 티커 제안(`ticker_suggestion`, low 확신도, 종목표에 자동 저장하지 않음)
- 청약기간은 투자기간이 아니다
- 원금 손실 한도 표시(`원금지급형`, `ELB`, `손실률(최대) 0%`)는 `max_loss_text`에, 상품명은 `product_name_text`에
- 'Hi-Five', 'Ultra' 같은 브랜드명으로 구조를 판단하지 않는다. 모델이 지원하는 구조(월지급식·리자드·녹아웃·ELB·스텝다운 등)를 미지원으로 보고해도 `normalize.py`가 걸러낸다

### 7.5 표기 정규화 (`normalize.py`)
| 원문 예 | 정규화 |
|---|---|
| `90-90-85-85-80-75(50)`, `.../ KI 40` | 배리어 목록 + `ki_barrier` |
| `NO KI`, `노낙인` | `ki_mode = "none"` |
| `최초기준가격의 50% 미만으로 하락한 적이 있는 경우` | `daily`, 0.50 |
| `만기평가일에 50% 미만` (기간 중 관찰 문구 없음) | `maturity` |
| `매월 말일 종가 기준 50% 미만`, `KI 45 (월말 관찰)` | `monthly` (`월지급`은 월말 관찰이 아님) |
| `연 8.0%`, `6개월마다 4%`, `월 0.51%` | 연 쿠폰 (월은 ×12). `월 0.51%, 연 6.12%`처럼 함께 있으면 '연' 값 우선 |
| `3년`, `1년 6개월`, `투자기간 2026/10/30 ~ 2029/11/02` | 만기 개월 수 (날짜는 가장 가까운 개월) |
| 평가 주기 미기재 | `만기 ÷ 배리어 개수` (low 확신도) |
| `월지급 65` | `monthly_coupon_barrier = 0.65` |
| `90(L75)-90(L70)-85-80-75-70` | 배리어 + `lizard_barriers = [0.75, 0.70]`. `리자드 75`처럼 값 하나면 전 회차 |
| `손실률(최대) 0%`, `원금지급형`, `ELB` / `원금 90% 보장` / `-100%` | `max_loss` = 0 / 0.1 / 1.0 |
| 손실률 표기 없음 + 상품명에 `ELB`·`DLB` | `max_loss = 0` (상품명이 `ELS`면 비움) |
| `행사가 100%`, `참여율 100%`, `낙아웃 137%`, `낙아웃발생시 수익률 3%` | `strike`, `participation`, `ko_upper`(100% 미만이면 `ko_lower`), `rebate` |
| 녹아웃형의 `연 36.00% (세전 최대)` | 최대 수익률이므로 쿠폰으로 쓰지 않음 |
| `코스피200`, `S&P 500 지수`, `Eurostoxx50` | 종목표 이름(`KOSPI200`, `S&P500`, `EUROSTOXX50`) |

- 표기 해석은 레이아웃이 아니라 키워드와 값 형식으로 한다
- 배리어 개수와 평가 횟수가 다르면 값을 고치지 말고 경고만 남긴다
- 숫자 단위: `%`가 있으면 %, 숫자만 있으면 필드별 기준(배리어·쿠폰 1, 행사가·참여율·녹아웃 3)보다 크면 %

### 7.6 시장 데이터 (`market.py`, `rates.py`, `data/underlyings.json`)
- 종목표: `{ "이름": {"ticker", "vol", "div", "aliases", "div_ticker"(선택), "note"(선택)} }`. 삼성전자 `005930.KS`, SK하이닉스 `000660.KS`, KOSPI200 `069500.KS`(Yahoo `^KS200` 시세가 비어 있어 KODEX 200 ETF로 대체), S&P500 `^GSPC`, EUROSTOXX50 `^STOXX50E`, 닛케이225 `^N225`. 종목 추가는 이 파일에 항목만 넣는다
- 기초자산 이름을 입력하거나 자동 입력되면 종목표의 티커·기본 변동성·배당을 채우고 "(기본값)" 표기. 표에 없으면 빈칸(제안 티커만)
- **시세로 추정**: 추정 기간(개월, 2~120, 기본 12)의 일별 종가로 실현변동성(로그수익률 표준편차 × √252)·상관계수. 공통 거래일만. "(과거 N개월 기준)" 표기
- **배당**: 같은 버튼에서 최근 1년 실제 배당금 합계 ÷ 현재 주가. Yahoo 요약값(`dividendYield`)은 단위가 섞여 쓰지 않는다. 지수는 `div_ticker`(S&P500→SPY, EUROSTOXX50→FEZ)로 계산, 닛케이225는 대체 ETF가 없어 기본값 유지
- **금리**: "국고채 3년 불러오기"가 ECOS(통계 `817Y002`, 항목 `010200000`)의 최근 10일 중 가장 최근 값을 채움. 키는 `ECOS_API_KEY`. ECOS가 실패하면(키 없음 포함) 네이버 금융(`KR3YT=RR`, 키 없음)의 값을 쓰고 출처를 표시한다. 네이버 주소는 비공식이라 응답을 검사하고, 형식이 다르면 값을 넣지 않는다. 둘 다 실패하면 ECOS 오류를 안내한다
- 모두 실패하면 안내만 띄우고 기존 값을 유지한다. 붙여넣은 종가도 같은 계산 함수를 쓴다
- 캐시: 시세 하루, 금리 6시간

---

## 8. 비용·남용 방지
공개 링크라 누구나 접속할 수 있다는 전제로 만든다.
- 세션당 자동 입력 횟수 `MAX_EXTRACTIONS_PER_SESSION`(기본 5). 넘으면 업로드 비활성화(샘플 체험은 캐시라 가능)
- 선택적 접근 코드 `ACCESS_CODE`: 있으면 자동 입력 전에 묻는다. 수동 입력·평가는 항상 가능
- 업로드 10MB, PDF 3페이지, 추출 응답 최대 토큰 4096
- 평가·민감도·샘플 추출 결과 캐시
- Claude로 전환하면 Anthropic 콘솔에서 프로젝트 전용 키에 월 사용 한도를 건다

---

## 9. 설정값 (`.streamlit/secrets.toml`, `elsa/config.py`)

```toml
EXTRACTOR = "gemini"                      # "gemini" 또는 "claude"
EXTRACTOR_MODEL = "gemini-flash-lite-latest"
GEMINI_API_KEY = ""
ANTHROPIC_API_KEY = ""                    # EXTRACTOR="claude"일 때만
ECOS_API_KEY = ""                         # 국고채 3년 금리. 비우면 네이버 금융 값 사용
ACCESS_CODE = ""                          # 비우면 접근 코드 없음
MAX_EXTRACTIONS_PER_SESSION = 5
```

`config.py`는 `st.secrets`를 먼저 보고, 없으면 환경변수를 본다.

---

## 10. 화면 구성 (`app.py`)

위에서 아래로:
1. 제목, 한 줄 소개, 사용법 3단계
2. 이미지 자동 입력 (업로드·조건 읽기, 샘플 선택·체험, 개인정보 안내, 사용 횟수)
3. 자동 입력 안내 → 상품 유형 선택 → 상품 조건 폼
   - 자동조기상환형: 기초자산 / 만기·평가 주기·상환 배리어 / 쿠폰·월지급식·리자드 / 낙인·더미 쿠폰 / 최대 손실률
   - 녹아웃형: 기초자산 / 만기·행사가·참여율 / 상승·하락 녹아웃 / 관찰·리베이트·최소 수익률 / 최대 손실률
4. 시장 데이터: 종목별 티커·변동성·배당, 상관계수, 금리(국고채 3년 불러오기), 추정 기간·시세로 추정, 종가 붙여넣기
5. 시뮬레이션 설정 (경로 수, 시드, 적정 쿠폰 목표 공정가치) — 접어 둠
6. 평가하기 → 공정가치·판정 문구·적정 쿠폰, 핵심 지표, 회차별 상환 확률 표·차트(녹아웃은 녹아웃 확률 등)
7. 민감도 분석 (상관 ±0.1, 변동성 ±5%p, 금리 ±0.5%p)
8. 모형 가정과 한계, 면책 문구, 사용 중인 추출기

1의 제목·소개 아래에 탭을 둔다. **ELS 평가** 탭이 사용법 3단계와 2~7, 모형 가정을 담고, **엔진 검증** 탭은 입력(S0·K·σ·r·q·T·N·시드), 정답 비교, 수렴 그래프, 일괄 검증, 대조변수 적용 효과, ELS 로직 점검을 보여 준다. 면책 문구와 추출기는 탭 아래에 공통으로 둔다. `st.tabs`는 보이지 않는 탭도 매 실행 그리므로 엔진 검증은 "검증 실행"을 눌렀을 때만 계산한다.

첫 접속 시 폼은 기준값 예시(삼성전자/SK하이닉스)로 채우고 예시라고 표시한다.
판정 문구: 공정가치 < 94% "공정가치가 낮음 · 위험 대비 쿠폰이 부족한 조건", 94~99.5% "일반적인 발행 수준", 그 이상 "액면에 근접 · 투자자에게 유리한 조건".

---

## 11. 검증 규칙 (`validation.py`)

- 기초자산 1~3개, 이름 비어 있지 않음
- 만기·평가 주기 > 0, 나누어떨어짐. 배리어 개수 = 평가 횟수, 모든 배리어 > 0
- 쿠폰 ≥ 0(비어도 됨). 낙인 방식이 있으면 낙인 배리어 > 0, 낙인 ≥ 만기 배리어면 경고
- 월지급 배리어 0~150%. 최대 손실률 0~100%
- 리자드: 개수 ≤ 평가 횟수, 값 0~150%, 월지급식과 동시 불가, 리자드 ≥ 그 회차 상환 배리어면 경고
- 녹아웃형: 배리어 하나 이상, `ko_upper > strike`, `ko_lower < strike`, 참여율·리베이트·최소 수익률 ≥ 0, 최소 수익률 > 0이면 최대 손실률은 0
- 변동성 > 0(150% 초과는 경고), 상관계수 −1~1·대칭·대각 1·양의 정부호, 금리 범위 밖이면 경고
- 경로 수 1만~10만 (ELS 평가. 엔진 검증 탭은 1,000~500,000)

오류 메시지는 무엇을 어떻게 고치면 되는지 한국어로 쓴다. 예: "평가가 6회이므로 상환 배리어를 6개 입력해 주세요. 지금은 5개입니다."

---

## 12. 작업 규칙

- 기능을 바꾸면 테스트를 같이 고치거나 추가해 통과시킨다. 엔진을 바꾸면 기준값 테스트를 반드시 돌린다
- **모든 `.py` 파일 맨 위에 docstring**: 파일의 목적 + 주요 함수·클래스와 기능 요약. 함수를 추가·삭제하면 docstring 목록도 고친다
- 추출기 테스트는 실제 API를 부르지 않고 `tests/fixtures/`의 응답 JSON으로 검증한다. 실제 API·네트워크 호출은 `@pytest.mark.live`
- `elsa/` 패키지는 streamlit을 import하지 않는다 (`config.py`의 함수 내부 조회만 예외)
- UI 문구와 오류 메시지는 한국어. 주석은 한국어, 식별자는 영어
- 업로드 이미지는 디스크에 저장하지 않는다
- `@st.cache_data` 함수는 dict·list·숫자 같은 기본 타입만 반환한다. 앱 실행 중 코드가 다시 불러와지면 클래스가 바뀌어 dataclass·Pydantic 객체는 캐시 저장(pickle)이 실패한다
- `elsa/` 패키지를 고친 뒤 화면에서 확인할 때는 Streamlit 서버를 **재시작**한다. Streamlit은 바뀐 파일만 다시 불러오므로, 바뀌지 않은 모듈(예: `gemini.py`)이 받아 둔 옛 함수를 계속 쓸 수 있다. 브라우저 새로고침으로는 해결되지 않는다
- 샘플 이미지에는 실존 증권사·상품명·로고를 쓰지 않는다("예시증권 제1호 ELS" 같은 가상 이름)
- 파이썬 외 단계(git, 배포 등)는 사용자에게 쉬운 말로 설명한다

## 13. 자주 쓰는 명령

```powershell
.\.venv\Scripts\Activate.ps1          # 가상환경 켜기
pip install -r requirements-dev.txt   # 앱 + 테스트 도구 설치
streamlit run app.py                  # http://localhost:8501
pytest                                # 실제 API·네트워크 호출 제외
pytest -m live                        # 실제 Gemini·Yahoo·ECOS·네이버 금융 호출 포함
python scripts\make_sample.py         # 샘플 이미지 다시 만들기
```

## 14. 배포 (Streamlit Community Cloud)

1. GitHub 저장소 `018lim/elsa`(공개)에 push. push 전에 `secrets.toml`, `tests/fixtures/images/`, `KakaoTalk_*`가 커밋 대상에 없는지, 커밋할 파일에 실제 키 값이 없는지 확인
2. share.streamlit.io에서 저장소 연결, 메인 파일 `app.py`, Python 3.11
3. 앱 설정의 Secrets에 9장 설정값 입력 (로컬 키와 별도로 발급한 키를 권장)
4. 배포 URL을 README 상단에 추가
- `requirements.txt`는 버전을 고정하고 실제로 쓰는 패키지만 넣는다
- 클라우드에서 Yahoo·ECOS 접속이 막히면 안내 문구만 뜨고 기본값으로 계속 쓸 수 있어야 한다
- 실제로 Community Cloud에서는 ECOS 접속이 제한 시간 초과로 실패한다(2026-11-01 확인, 같은 시점 국내 PC에서는 정상, Yahoo는 Cloud에서 정상). 그래서 조회 제한 시간을 5초로 두고, 실패하면 네이버 금융을 조회한다

## 15. 범위 밖 (v1에서 하지 않음)

4개 이상 기초자산, 녹아웃 즉시 상환(현재는 만기 일시 지급만), 내재변동성(변동성 지수 포함)·변동성 곡면·금리 기간구조·이산배당, 발행사 신용위험, 세금·수수료, 로그인·저장 기능. 이미지에서 지원하지 않는 구조가 발견되면 경고만 띄운다.
