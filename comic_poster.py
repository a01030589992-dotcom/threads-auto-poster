"""
꿀템연구소 웹툰 자동 포스터 v2
──────────────────────────────────────────────────────────────
1080×1350 단일 이미지에 6컷 웹툰 레이아웃 (슬라이드 → 진짜 만화)
네이버 쇼핑 실제 상품 + Claude 스크립트 → Threads 단일 이미지 포스팅

필요 패키지: pip install anthropic requests pillow
"""

import os
import re
import json
import time
import base64
import textwrap
import requests
from pathlib import Path
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import anthropic
import naver_shopping

# ── 경로 ──────────────────────────────────────────────────────
SCRIPT_DIR     = Path(__file__).parent
CHARACTER_PATH = SCRIPT_DIR / "character.png"

# ── 환경 변수 ─────────────────────────────────────────────────
CLAUDE_API_KEY       = os.environ.get("CLAUDE_API_KEY", "")
THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
THREADS_USER_ID      = os.environ.get("THREADS_USER_ID", "")
GITHUB_TOKEN         = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO          = "a01030589992-dotcom/threads-auto-poster"
GITHUB_BRANCH        = "main"

# ── 캔버스 치수 ───────────────────────────────────────────────
W      = 1080   # 전체 너비
H      = 1350   # 전체 높이 (4:5 비율 — Threads/Instagram 최적)
MARGIN = 16     # 외곽 여백
GUTTER = 10     # 패널 사이 간격
BG     = "#ECECEC"  # 캔버스 배경 (만화책 그레이)

# ── 패널 레이아웃 ─────────────────────────────────────────────
# 총 6컷: [전체폭] / [반반] / [전체폭] / [반반] + 하단 로고바
HALF_W = (W - MARGIN * 2 - GUTTER) // 2   # 519px

ROW1_H = 270   # 인트로 (전체폭)
ROW2_H = 300   # 공감/위기 (반반)
ROW3_H = 255   # 반전 (전체폭)
ROW4_H = 265   # 상품/CTA (반반)
LOGO_H = 110   # 하단 로고바

# y 시작점
Y1    = MARGIN                              # 16
Y2    = Y1 + ROW1_H + GUTTER               # 296
Y3    = Y2 + ROW2_H + GUTTER               # 606
Y4    = Y3 + ROW3_H + GUTTER               # 871
YLOGO = Y4 + ROW4_H + GUTTER               # 1146

# 각 패널 (x, y, w, h)
PANEL_RECTS = [
    (MARGIN,                    Y1, W - MARGIN * 2, ROW1_H),  # 0 intro   전체폭
    (MARGIN,                    Y2, HALF_W,          ROW2_H),  # 1 problem 좌
    (MARGIN + HALF_W + GUTTER,  Y2, HALF_W,          ROW2_H),  # 2 crisis  우
    (MARGIN,                    Y3, W - MARGIN * 2, ROW3_H),  # 3 reveal  전체폭
    (MARGIN,                    Y4, HALF_W,          ROW4_H),  # 4 solution 좌
    (MARGIN + HALF_W + GUTTER,  Y4, HALF_W,          ROW4_H),  # 5 cta     우
]

# 패널별 배경색 (파스텔)
PANEL_BG = [
    "#FFFDE7",   # 0 intro    — 연노랑
    "#FFF3E0",   # 1 problem  — 연주황
    "#F3E5F5",   # 2 crisis   — 연보라
    "#E8F5E9",   # 3 reveal   — 연초록
    "#E3F2FD",   # 4 solution — 연파랑
    "#FFF9C4",   # 5 cta      — 밝은노랑
]

# 패널별 말풍선 여부 / 캐릭터 표시 여부
SHOW_BUBBLE = [True,  True,  False, True,  False, True ]
SHOW_CHAR   = [True,  True,  False, True,  False, True ]

BORDER_CLR = "#1A1A1A"
BUBBLE_CLR = "#FFFFFF"
TEXT_CLR   = "#111111"
ACCENT_CLR = "#E53935"   # 위기 컷 강조색

# ── 카테고리 ──────────────────────────────────────────────────
CATEGORIES = [
    {
        "name":        "자취생 필수템",
        "link":        "https://link.coupang.com/a/e0J5NRuVIy",
        "naver_query": "자취 필수템 생활용품",
    },
    {
        "name":        "여름 시즌 아이템",
        "link":        "https://link.coupang.com/a/e0J8XB3t7s",
        "naver_query": "여름 더위 냉감 용품",
    },
    {
        "name":        "주방가전",
        "link":        "https://link.coupang.com/a/e0KcjeIb7I",
        "naver_query": "자취 소형 주방가전",
    },
    {
        "name":        "영양제/건강식품",
        "link":        "https://link.coupang.com/a/e0Ke9Db6uy",
        "naver_query": "20대 직장인 영양제",
    },
]


# ── 폰트 로드 ─────────────────────────────────────────────────
def load_fonts():
    candidates_bold = [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ]
    candidates_reg = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    ]
    bold = next((p for p in candidates_bold if Path(p).exists()), None)
    reg  = next((p for p in candidates_reg  if Path(p).exists()), None)

    def fnt(path, size):
        if path:
            return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    return {
        "title":     fnt(bold, 44),
        "bubble":    fnt(reg,  30),
        "small":     fnt(reg,  22),
        "logo":      fnt(bold, 26),
        "panel_num": fnt(bold, 20),
        "crisis":    fnt(bold, 36),
    }


# ── Claude: 웹툰 스크립트 생성 ────────────────────────────────
def generate_script(category: dict, products: list[dict]) -> dict:
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    product_block = naver_shopping.format_for_prompt(products)

    prompt = f"""
너는 '꿀템연구소' 연구원 캐릭터의 6컷 웹툰 작가야.
캐릭터 설정: 소비생활 덕후 연구원. 쿨하고 솔직함. 실패도 공유함. 광고 아님.
카테고리: {category['name']}

{f"오늘 네이버 쇼핑 실제 상품:{chr(10)}{product_block}" if product_block else ""}

아래 JSON만 출력해. 설명 없이.

[웹툰 대사 작성 규칙]
- 실제 20대가 혼잣말하듯 써 (속마음, 리얼 반응)
- 연구원 캐릭터라서 "가설", "실험", "연구 결과" 유머 가능
- "ㄹㅇ", "팩트", "근데 진짜", "반박불가" 자연스럽게
- 광고 느낌 대사 절대 금지: "추천", "강추", "놓치지 마", "클릭"
- crisis 컷: 독자가 "아 맞아 나도..." 하는 공감 팩폭 한 방
- 각 컷 대사는 짧고 임팩트있게 (최대 25자)

흐름: 1일상공감 → 2문제터짐 → 3팩폭/충격 → 4발견 → 5상품+가격 → 6찐후기CTA

{{
  "title": "클릭하고 싶은 제목 (15자이내, 숫자나 반전 활용)",
  "panels": [
    {{"type": "intro",    "text": "일상 공감 혼잣말 (20자이내)", "has_char": true}},
    {{"type": "problem",  "text": "문제 상황 리얼 반응 (22자이내)", "has_char": true}},
    {{"type": "crisis",   "text": "팩폭 한 방 나레이션 (20자이내)", "has_char": false}},
    {{"type": "reveal",   "text": "발견 순간 리얼 반응 (22자이내)", "has_char": true}},
    {{"type": "solution", "text": "상품명 + 가격 + 한줄평 (28자이내)", "has_char": false}},
    {{"type": "cta",      "text": "찐후기 마무리 (20자이내)", "has_char": true}}
  ],
  "caption": "스레드 본문 (150자, 20대 혼잣말체, 실패담 or 반전 포함, 마지막: 링크는 댓글에 👇)",
  "product_highlight": "핵심상품명 15자이내"
}}
"""
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    m   = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"JSON 파싱 실패:\n{raw[:300]}")
    return json.loads(m.group())


# ── 말풍선 그리기 ─────────────────────────────────────────────
def draw_bubble(draw, text, px, py, pw, ph, font, tail=True):
    """패널 위쪽에 말풍선 배치"""
    pad   = 14
    bub_h = int(ph * 0.42)
    bx1 = px + pad
    by1 = py + pad
    bx2 = px + pw - pad
    by2 = py + bub_h

    draw.rounded_rectangle(
        [bx1, by1, bx2, by2],
        radius=18,
        fill=BUBBLE_CLR,
        outline=BORDER_CLR,
        width=2,
    )

    if tail:
        cx = (bx1 + bx2) // 2
        ty = by2
        draw.polygon([(cx - 14, ty), (cx + 14, ty), (cx, ty + 22)], fill=BUBBLE_CLR)
        draw.line([(cx - 14, ty), (cx, ty + 22)], fill=BORDER_CLR, width=2)
        draw.line([(cx + 14, ty), (cx, ty + 22)], fill=BORDER_CLR, width=2)

    avail_w = bx2 - bx1 - pad * 2
    char_w  = max(font.size * 9 // 10, 1)
    max_c   = max(8, avail_w // char_w)
    wrapped = textwrap.fill(text, width=max_c)
    mid_x   = (bx1 + bx2) // 2
    mid_y   = (by1 + by2) // 2
    draw.multiline_text(
        (mid_x, mid_y), wrapped,
        font=font, fill=TEXT_CLR,
        anchor="mm", align="center", spacing=6,
    )


# ── 나레이션 박스 그리기 (위기/설명 컷) ──────────────────────
def draw_narration(draw, text, px, py, pw, ph, font, accent=False):
    """배경 위 나레이션 텍스트"""
    pad     = 20
    color   = ACCENT_CLR if accent else TEXT_CLR
    char_w  = max(font.size * 9 // 10, 1)
    max_c   = max(8, (pw - pad * 2) // char_w)
    wrapped = textwrap.fill(text, width=max_c)

    bx1 = px + pad; by1 = py + pad
    bx2 = px + pw - pad; by2 = py + ph - pad
    draw.rectangle([bx1, by1, bx2, by2], fill="#00000018")
    draw.multiline_text(
        (px + pw // 2, py + ph // 2), wrapped,
        font=font, fill=color,
        anchor="mm", align="center", spacing=10,
    )


# ── 웹툰 단일 이미지 생성 ─────────────────────────────────────
def create_webtoon_image(script: dict, category: dict) -> bytes:
    fonts = load_fonts()

    # 캐릭터 이미지 로드
    char_img = None
    if CHARACTER_PATH.exists():
        try:
            char_img = Image.open(CHARACTER_PATH).convert("RGBA")
            print(f"  ✅ 캐릭터 이미지 로드: {char_img.size}")
        except Exception as e:
            print(f"  ⚠️  캐릭터 로드 실패: {e}")

    canvas = Image.new("RGB", (W, H), BG)
    draw   = ImageDraw.Draw(canvas)
    panels = script.get("panels", [])

    for idx, (px, py, pw, ph) in enumerate(PANEL_RECTS):
        if idx >= len(panels):
            break
        panel = panels[idx]
        text  = panel.get("text", "")
        ptype = panel.get("type", "")

        # ① 패널 배경 + 테두리
        draw.rectangle(
            [px, py, px + pw, py + ph],
            fill=PANEL_BG[idx], outline=BORDER_CLR, width=3,
        )

        # ② 컷 번호 (좌상단 원)
        num_r = 16
        nx, ny = px + 10, py + 10
        draw.ellipse([nx, ny, nx + num_r * 2, ny + num_r * 2], fill=BORDER_CLR)
        draw.text((nx + num_r, ny + num_r), str(idx + 1),
                  font=fonts["panel_num"], fill="#FFFFFF", anchor="mm")

        # ③ 캐릭터 이미지 (패널 하단)
        if char_img and panel.get("has_char") and SHOW_CHAR[idx]:
            char_h = int(ph * 0.52)
            ratio  = char_img.width / char_img.height
            char_w = min(int(char_h * ratio), pw - 20)
            c      = char_img.resize((char_w, char_h), Image.LANCZOS)
            cx_pos = px + (pw - char_w) // 2
            cy_pos = py + ph - char_h - 4
            canvas.paste(c, (cx_pos, cy_pos), c)

        # ④ 텍스트
        if not text:
            continue

        if SHOW_BUBBLE[idx] and panel.get("has_char"):
            tail_on = bool(char_img and panel.get("has_char") and SHOW_CHAR[idx])
            draw_bubble(draw, text, px, py, pw, ph, fonts["bubble"], tail=tail_on)
        elif ptype == "crisis":
            draw_narration(draw, text, px, py, pw, ph, fonts["crisis"], accent=True)
        else:
            draw_narration(draw, text, px, py, pw, ph, fonts["bubble"])

    # ── 하단 로고바 ───────────────────────────────────────────
    lx1 = MARGIN;       ly1 = YLOGO
    lx2 = W - MARGIN;   ly2 = H - MARGIN
    draw.rectangle([lx1, ly1, lx2, ly2], fill="#FFF8E1", outline=BORDER_CLR, width=3)
    logo_cy = (ly1 + ly2) // 2
    draw.text((W // 2, logo_cy - 18), "🍯 꿀템연구소",
              font=fonts["logo"], fill="#5D4037", anchor="mm")
    product = script.get("product_highlight", "")
    if product:
        draw.text((W // 2, logo_cy + 18), f"오늘의 꿀템: {product}",
                  font=fonts["small"], fill="#888", anchor="mm")

    buf = BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── GitHub: 이미지 업로드 ─────────────────────────────────────
def upload_image(image_bytes: bytes, filename: str) -> str:
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/images/{filename}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
    }
    sha = None
    try:
        r = requests.get(api_url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    payload: dict = {
        "message": f"webtoon: {filename}",
        "content": base64.b64encode(image_bytes).decode(),
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=headers, json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub 업로드 실패: {r.status_code} {r.text[:200]}")

    raw_url = (
        f"https://raw.githubusercontent.com"
        f"/{GITHUB_REPO}/{GITHUB_BRANCH}/images/{filename}"
    )
    print(f"  ✅ GitHub 업로드 완료: {raw_url}")
    return raw_url


# ── Threads: 단일 이미지 포스팅 ───────────────────────────────
def post_image_to_threads(image_url: str, caption: str) -> str | None:
    res = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
        data={
            "media_type":   "IMAGE",
            "image_url":    image_url,
            "text":         caption,
            "access_token": THREADS_ACCESS_TOKEN,
        },
        timeout=20,
    )
    cid = res.json().get("id")
    if not cid:
        print(f"  ❌ 컨테이너 생성 실패: {res.json()}")
        return None
    print(f"  ✅ 이미지 컨테이너 생성: {cid}")
    time.sleep(5)

    pub = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
        data={"creation_id": cid, "access_token": THREADS_ACCESS_TOKEN},
        timeout=20,
    )
    pid = pub.json().get("id")
    if not pid:
        print(f"  ❌ 게시 실패: {pub.json()}")
        return None
    print(f"  ✅ Threads 게시 완료: {pid}")
    return pid


# ── Threads: 댓글(쿠팡 링크) ─────────────────────────────────
def post_link_comment(post_id: str, link: str) -> bool:
    comment = (
        "👇 상품 링크\n"
        f"{link}\n\n"
        "※ 이 포스팅은 쿠팡 파트너스 활동의 일환으로,\n"
        "이에 따른 일정액의 수수료를 제공받습니다."
    )
    res = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
        data={
            "media_type":   "TEXT",
            "text":         comment,
            "reply_to_id":  post_id,
            "access_token": THREADS_ACCESS_TOKEN,
        },
        timeout=15,
    )
    cid = res.json().get("id")
    if not cid:
        return False
    time.sleep(2)
    pub = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
        data={"creation_id": cid, "access_token": THREADS_ACCESS_TOKEN},
        timeout=15,
    )
    ok = "id" in pub.json()
    if ok:
        print("  ✅ 링크 댓글 게시 완료")
    return ok


# ── 메인 ──────────────────────────────────────────────────────
def main():
    print(f"\n{'='*52}")
    print(f"🎨 꿀템연구소 웹툰 포스팅 시작: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*52}")

    now = datetime.now()
    cat = CATEGORIES[(now.day + now.hour // 8) % len(CATEGORIES)]
    print(f"\n📂 카테고리: {cat['name']}  (날짜:{now.day} 시간대:{now.hour//8})")

    # 1. 네이버 쇼핑 상품 수집
    print(f"\n🛍️  네이버 쇼핑 수집 중... 검색어: [{cat['naver_query']}]")
    products = naver_shopping.fetch_products(cat["naver_query"], count=5)
    if products:
        print(f"  ✅ 수집 완료 {len(products)}개:")
        for p in products:
            print(f"     ✔ {p['name']} — {p['price']}")
    else:
        print("  ⚠️  상품 미수집 — 기본 내용으로 진행")

    # 2. 스크립트 생성
    print("\n✍️  웹툰 스크립트 생성 중 (Claude Sonnet)...")
    script = generate_script(cat, products)
    print(f"  제목: {script.get('title')}")
    print(f"  핵심상품: {script.get('product_highlight')}")
    for i, p in enumerate(script.get("panels", []), 1):
        print(f"  [{i}] {p.get('type',''):10s}| {p.get('text','')[:40]}")

    # 3. 웹툰 이미지 생성
    print("\n🖼️  웹툰 이미지 생성 중...")
    image_bytes = create_webtoon_image(script, cat)
    print(f"  이미지: {W}×{H}px  {len(image_bytes)//1024}KB")

    # 4. GitHub 업로드
    filename = f"webtoon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    print(f"\n📤 GitHub 업로드: images/{filename}")
    raw_url = upload_image(image_bytes, filename)

    print("  ⏳ CDN 반영 대기 (20초)...")
    time.sleep(20)

    # 5. Threads 게시
    caption = script.get(
        "caption",
        f"꿀템연구소 [{cat['name']}] 오늘의 꿀템 🍯 링크는 댓글에 👇",
    )
    print(f"\n📱 Threads 게시 중...")
    print(f"  캡션: {caption[:60]}...")
    post_id = post_image_to_threads(raw_url, caption)
    if not post_id:
        print("❌ 게시 실패")
        return

    # 6. 링크 댓글
    time.sleep(3)
    print("\n💬 쿠팡 링크 댓글 추가 중...")
    post_link_comment(post_id, cat["link"])

    print(f"\n🎉 완료! [{cat['name']}] 웹툰 포스팅 성공")


if __name__ == "__main__":
    main()
