"""ELSA 계산·추출 로직 패키지.

화면(streamlit)과 분리된 순수 파이썬 코드만 둔다.
- schemas: 상품 조건·시장 데이터·평가 결과·추출 결과 데이터 모델
- pricing: 몬테카를로 평가 엔진과 민감도 분석
- validation: 수동·자동 입력 공통 조건 검증
- market: 종목표 조회, 시세 조회, 변동성·상관계수 추정
- extraction: 상품 안내 이미지 → 조건 추출 (Gemini / Claude)
"""
