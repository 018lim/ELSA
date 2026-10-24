"""체험용 가상 상품 안내 이미지 생성 (Pillow).

증권사 앱의 '상품정보' 카드와 비슷한 모양으로, 실존하지 않는 가상 상품(예시증권 제1호·제2호)을 그린다.
실존 증권사·상품명·로고는 쓰지 않는다. 한글 글꼴은 Windows의 맑은 고딕을 쓴다.

실행: .\\.venv\\Scripts\\python.exe scripts\\make_sample.py
- draw_card(title, rows, path): 제목과 (항목, 값) 행으로 카드 이미지를 그려 저장
- main(): samples/sample_termsheet.png(자동조기상환형), samples/sample_knockout.png(녹아웃형) 생성
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = "C:/Windows/Fonts/malgun.ttf"
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"

W = 1340
PAD = 60
LABEL_W = 300
TEXT = (30, 30, 30)
LABEL = (140, 140, 140)
CARD = (246, 246, 246)
NOTE = (170, 170, 170)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    """공백 기준으로 줄바꿈하되, 한 단어가 너무 길면 글자 단위로 자른다."""
    lines, line = [], ""
    for word in text.split(" "):
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        line = word
        while draw.textlength(line, font=font) > width:
            cut = len(line)
            while draw.textlength(line[:cut], font=font) > width:
                cut -= 1
            lines.append(line[:cut])
            line = line[cut:]
    if line:
        lines.append(line)
    return lines


def draw_card(title: str, subtitle: str, rows: list[tuple[str, str]], path: Path) -> None:
    f_title = ImageFont.truetype(FONT_BOLD, 64)
    f_sub = ImageFont.truetype(FONT, 34)
    f_label = ImageFont.truetype(FONT, 44)
    f_value = ImageFont.truetype(FONT, 44)
    line_h = 66
    value_w = W - 2 * PAD - LABEL_W - 80

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    wrapped = [(label, _wrap(probe, value, f_value, value_w)) for label, value in rows]
    card_h = sum(max(1, len(v)) * line_h + 48 for _, v in wrapped) + 80
    height = 220 + card_h + 120

    img = Image.new("RGB", (W, height), "white")
    d = ImageDraw.Draw(img)
    d.text((PAD - 40, 50), title, font=f_title, fill=TEXT)
    d.text((PAD - 40, 135), subtitle, font=f_sub, fill=LABEL)
    top = 210
    d.rounded_rectangle((PAD - 45, top, W - PAD + 15, top + card_h), radius=48, fill=CARD)

    y = top + 70
    for label, lines in wrapped:
        d.text((PAD + 40, y), label, font=f_label, fill=LABEL)
        for i, line in enumerate(lines):
            x = W - PAD - 40 - d.textlength(line, font=f_value)
            d.text((x, y + i * line_h), line, font=f_value, fill=TEXT)
        y += max(1, len(lines)) * line_h + 48

    d.text((PAD - 40, top + card_h + 40), "※ 체험용 가상 상품입니다. 실존하는 증권사·상품이 아닙니다.", font=f_sub, fill=NOTE)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    print("saved", path.relative_to(ROOT), img.size)


def main() -> None:
    draw_card(
        "상품정보",
        "예시증권 제1호 ELS (자동조기상환형)",
        [
            ("기초자산", "삼성전자, SK하이닉스"),
            ("투자기간", "2026/11/02 ~ 2029/11/02"),
            ("평가주기", "6개월마다 조기상환 평가"),
            ("상환조건", "90-90-85-85-80-75 / KI 50"),
            ("수익률", "세전 연 8.0%"),
            ("낙인조건", "투자기간 중 최초기준가격의 50% 미만으로 하락한 적이 있는 경우"),
            ("더미쿠폰", "낙인 미발생 시 만기 배리어 미달이어도 연 8.0% 지급"),
            ("투자위험도", "매우높은위험"),
        ],
        ROOT / "samples" / "sample_termsheet.png",
    )
    draw_card(
        "상품정보",
        "예시증권 제2호 ELB (녹아웃형)",
        [
            ("기초자산", "KOSPI200"),
            ("투자기간", "2026/11/02 ~ 2027/11/02"),
            ("상환조건", "행사가 100%, 참여율 80%, 낙아웃 130%, 낙아웃발생시 수익률 2.5%"),
            ("관찰방식", "투자기간 중 종가 기준 한번이라도 130% 이상이면 낙아웃"),
            ("투자위험도", "낮은위험"),
        ],
        ROOT / "samples" / "sample_knockout.png",
    )


if __name__ == "__main__":
    main()
