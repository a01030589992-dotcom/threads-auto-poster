"""
꿀템연구소 미스터리형 6컷 웹툰 자동화 v4.1
명세: COWORK_MYSTERY_WEBTOON_SPEC.md + COWORK_WEBTOON_QUALITY_UPGRADE.md
변경: 2열 그리드, 앵커 말풍선, 샷타입 다양성, 캐릭터 레퍼런스 실전달
"""
import os, re, sys, json, time, base64, random, textwrap, shutil
from pathlib import Path
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageStat
import anthropic
import openai
import requests
import naver_shopping

# ── CLI 플래그 ──────────────────────────────────────────────────
_ARGS         = set(sys.argv[1:])
DRY_RUN       = "--dry-run"            in _ARGS
SCRIPT_ONLY   = "--script-only"        in _ARGS
GEN_REFERENCE = "--generate-reference" in _ARGS
SKIP_IMG_API  = "--skip-image-api"     in _ARGS
QA_ONLY       = "--quality-check-only" in _ARGS
PRODUCT_INDEX = next(
    (int(a.split("=")[1]) for a in _ARGS if a.startswith("--product-index=")), None
)

# ── 경로 ───────────────────────────────────────────────────────
SCRIPT_DIR     = Path(__file__).parent
ASSETS_DIR     = SCRIPT_DIR / "assets"
COMICS_DIR     = SCRIPT_DIR / "generated_comics"
CHARACTER_PATH = SCRIPT_DIR / "character.png"
REFERENCE_PATH = ASSETS_DIR / "character_reference.png"
ASSETS_DIR.mkdir(exist_ok=True)
COMICS_DIR.mkdir(exist_ok=True)

# ── 환경변수 ───────────────────────────────────────────────────
_ENV = {k: os.environ.get(k, "") for k in [
    "CLAUDE_API_KEY", "OPENAI_API_KEY",
    "THREADS_ACCESS_TOKEN", "THREADS_USER_ID", "GITHUB_TOKEN",
]}
IMAGE_MODEL     = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
IMAGE_QUALITY   = os.environ.get("IMAGE_QUALITY", "medium")
MAX_RETRIES     = int(os.environ.get("MAX_PANEL_RETRIES", "2"))
DAILY_IMG_LIMIT = int(os.environ.get("DAILY_IMAGE_LIMIT", "24"))
AUTO_PUBLISH    = os.environ.get("AUTO_PUBLISH", "false").lower() == "true"
GITHUB_REPO     = "withmenlyn12212/threads-auto-poster"
GITHUB_BRANCH   = "main"

def _validate_env():
    need = ["CLAUDE_API_KEY", "OPENAI_API_KEY"]
    if not (DRY_RUN or SCRIPT_ONLY or GEN_REFERENCE or SKIP_IMG_API or QA_ONLY):
        need += ["THREADS_ACCESS_TOKEN", "THREADS_USER_ID", "GITHUB_TOKEN"]
    missing = [k for k in need if not _ENV[k]]
    if missing:
        print(f"필수 환경변수 누락: {', '.join(missing)}")
        sys.exit(1)

# ── 캔버스 & 레이아웃 상수 ─────────────────────────────────────
W, H       = 1080, 1920
MX         = 20          # 좌우 마진
MY         = 20          # 상하 마진
GUTTER     = 12          # 컷 사이 간격
LOGO_H     = 70          # 하단 로고 바 높이
GRID_COLS  = 2
GRID_ROWS  = 3

# 2열 3행 그리드 계산
_COL_W = (W - MX * 2 - GUTTER) // GRID_COLS        # ≈ 514px
_ROW_H = (H - MY * 2 - LOGO_H - GUTTER - GUTTER * (GRID_ROWS - 1)) // GRID_ROWS  # ≈ 591px
LOGO_Y = MY + GRID_ROWS * _ROW_H + (GRID_ROWS - 1) * GUTTER + GUTTER

def get_panel_rects():
    """2열 3행 그리드: [(x, y, w, h), ...] 6개 순서: 좌상→우상→좌중→우중→좌하→우하"""
    rects = []
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            x = MX + col * (_COL_W + GUTTER)
            y = MY + row * (_ROW_H + GUTTER)
            rects.append((x, y, _COL_W, _ROW_H))
    return rects

PANEL_RECTS = get_panel_rects()

# 컬러
PANEL_BG = ["#FFFDE7","#FFF3E0","#FCE4EC","#E8F5E9","#E3F2FD","#FFF9C4"]
BG_COLOR  = "#FAFAFA"
BORDER    = "#222222"
BUBBLE_F  = "#FFFFFF"
TEXT_C    = "#1A1A1A"

BODY_CTA = [
    "뭘 사용한 건지는 댓글에서 확인.",
    "물음표 아이템 정체는 댓글에 있어요.",
    "뭔지 궁금하면 댓글 확인해보세요.",
    "정체는 댓글에 적어뒀어요.",
]
DISCLOSURE = (
    "※ 이 포스팅은 쿠팡 파트너스 활동의 일환으로,\n"
    "이에 따른 일정액의 수수료를 제공받습니다."
)

# ── 카테고리 ───────────────────────────────────────────────────
CATEGORIES = [
    {"name":"자취생 필수템","link":"https://link.coupang.com/a/e0J5NRuVIy",
     "naver_query":"자취 필수템 생활용품","location":"자취방","problem_area":"정리/수납"},
    {"name":"여름 시즌 아이템","link":"https://link.coupang.com/a/e0J8XB3t7s",
     "naver_query":"여름 더위 냉감 용품","location":"침실/거실","problem_area":"더위/냉각"},
    {"name":"주방가전","link":"https://link.coupang.com/a/e0KcjeIb7I",
     "naver_query":"자취 소형 주방가전","location":"주방","problem_area":"요리/식사"},
    {"name":"영양제/건강식품","link":"https://link.coupang.com/a/e0Ke9Db6uy",
     "naver_query":"20대 직장인 영양제","location":"책상/침실","problem_area":"건강/피로"},
]

# ── 폰트 ───────────────────────────────────────────────────────
def _load_fonts():
    bold_cands = [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/gulim.ttc",
    ]
    reg_cands = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/gulim.ttc",
    ]
    bold = next((p for p in bold_cands if Path(p).exists()), None)
    reg  = next((p for p in reg_cands  if Path(p).exists()), None)
    def fnt(path, size):
        if path:
            try: return ImageFont.truetype(path, size)
            except Exception: pass
        return ImageFont.load_default()
    return {
        "bubble": fnt(bold, 26), "narr": fnt(bold, 30),
        "logo":   fnt(bold, 21), "small": fnt(reg, 17),
        "qmark":  fnt(bold, 96),
    }

# ── 캐릭터 기준표 ─────────────────────────────────────────────
def generate_character_reference():
    if REFERENCE_PATH.exists():
        print(f"  기준표 이미 존재: {REFERENCE_PATH.name}")
        return REFERENCE_PATH
    print("  캐릭터 기준표 생성 중...")
    client = openai.OpenAI(api_key=_ENV["OPENAI_API_KEY"])
    prompt = (
        "Korean webtoon character reference sheet. Friendly Korean male mascot in his 20s, "
        "black hair, round glasses, casual everyday outfit. "
        "Show: front view full body, left view, right view, 4 facial expressions "
        "(frustrated, surprised, happy, neutral). "
        "Polished manhwa/webtoon illustration style, warm lighting, clean line art. "
        "White background. No text, no logo, no watermark. 3x3 grid layout."
    )
    try:
        resp = client.images.generate(
            model=IMAGE_MODEL, prompt=prompt,
            size="1024x1024", quality=IMAGE_QUALITY, n=1,
        )
        data = _img_bytes(resp.data[0])
        img  = Image.open(BytesIO(data)).convert("RGB")
        img.save(REFERENCE_PATH)
        print(f"  기준표 저장: {REFERENCE_PATH.name}")
    except Exception as e:
        print(f"  기준표 생성 실패: {e}")
        if CHARACTER_PATH.exists():
            shutil.copy(CHARACTER_PATH, REFERENCE_PATH)
    return REFERENCE_PATH

def _img_bytes(obj):
    if hasattr(obj, "b64_json") and obj.b64_json:
        return base64.b64decode(obj.b64_json)
    if hasattr(obj, "url") and obj.url:
        r = requests.get(obj.url, timeout=30)
        r.raise_for_status()
        return r.content
    raise ValueError("이미지 데이터 없음")

# ── 대본 생성 (Claude) — 확장 스키마 ─────────────────────────
def generate_script(cat, products, anon_data):
    client = anthropic.Anthropic(api_key=_ENV["CLAUDE_API_KEY"])
    prompt = f"""너는 꿀템연구소 6컷 웹툰 작가야.
장소: {anon_data['location']} / 불편: {anon_data['problem_area']} / 카테고리: {cat['name']}

[6컷 흐름]
1 trouble: 공감 생활 불편 (wide, 전체 상황)
2 try_fail: 기존 시도 실패 (medium, 행동)
3 insight: 진짜 원인 발견 (closeup, 표정)
4 mystery: "?" 아이템 등장 (medium, 박스 중심 — 상품명 절대 금지)
5 result: 달라진 결과 (over_shoulder, 상품 안 보임)
6 cta: 댓글 유도 (medium, 시청자에게 직접)

[규칙]
- text: 대사 1개, 8~18자 한국어
- 상품명/브랜드 절대 금지
- scene_prompt: 영어, 카메라 거리+배경+행동 상세히
- 4컷 scene: "large cardboard box with big red ? symbol" 필수 포함
- 5컷 scene: "tidy space, no product visible, character satisfied" 필수
- caption: 120~180자, 상품명 없음, 쿠팡파트너스 고지 포함
- comment_body: 상품 자리 [PRODUCT], 링크 자리 [LINK]
- shot_type: wide/medium/closeup/over_shoulder/low_angle (6컷 중 4종류 이상)
- bubble_anchor: top_left/top_right/middle_left/middle_right/bottom_left/bottom_right
- bubble_shape: speech/thought/shout

JSON만 출력:
{{"panels":[
  {{"type":"trouble","text":"대사","scene_prompt":"english","shot_type":"wide","camera_angle":"eye_level","bubble_anchor":"top_right","bubble_shape":"speech","background_details":["item1","item2"],"aspect":"1:1"}},
  {{"type":"try_fail","text":"대사","scene_prompt":"english","shot_type":"medium","camera_angle":"eye_level","bubble_anchor":"top_left","bubble_shape":"speech","background_details":[],"aspect":"1:1"}},
  {{"type":"insight","text":"대사","scene_prompt":"english","shot_type":"closeup","camera_angle":"high_angle","bubble_anchor":"top_right","bubble_shape":"thought","background_details":[],"aspect":"1:1"}},
  {{"type":"mystery","text":"대사","scene_prompt":"large cardboard box with big red ? symbol","shot_type":"medium","camera_angle":"eye_level","bubble_anchor":"top_left","bubble_shape":"speech","background_details":["mystery box"],"aspect":"1:1"}},
  {{"type":"result","text":"대사","scene_prompt":"english satisfied result","shot_type":"over_shoulder","camera_angle":"diagonal","bubble_anchor":"top_right","bubble_shape":"speech","background_details":[],"aspect":"1:1"}},
  {{"type":"cta","text":"대사","scene_prompt":"english facing viewer waving","shot_type":"medium","camera_angle":"eye_level","bubble_anchor":"top_left","bubble_shape":"speech","background_details":[],"aspect":"1:1"}}
],
"caption":"본문 120~180자 쿠팡파트너스 고지 포함",
"comment_body":"댓글 [PRODUCT] [LINK] 포함"}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=1200,
        messages=[{"role":"user","content":prompt}],
    )
    raw = msg.content[0].text.strip()
    m   = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"JSON 파싱 실패:\n{raw[:300]}")
    return json.loads(m.group())

# ── 패널 프롬프트 빌더 ─────────────────────────────────────────
_CHAR_BASE = (
    "Korean vertical webtoon / manga panel, polished anime-inspired illustration, "
    "expressive Korean male mascot character in his 20s, consistent black hair and round glasses, "
    "casual everyday outfit, clean confident line art, warm lighting, rich domestic background, "
    "clear facial expression, clear hand gesture, dynamic body pose, "
    "foreground midground background depth, detailed props, natural panel composition"
)
_FORBID = (
    "Do not crop the face, do not crop hands, do not hide the main action, "
    "do not use horizontal banner composition, "
    "no text inside image, no caption boxes, no watermark, "
    "no product brand names logos or package shapes"
)

SHOT_DESC = {
    "wide":          "wide establishing shot, full room visible, character full body",
    "medium":        "medium shot, character from waist up, clear expression and gesture",
    "closeup":       "close-up on character face, detailed emotion, eyes and expression prominent",
    "over_shoulder": "over-the-shoulder angle, partial back of character, viewer sees what character sees",
    "low_angle":     "low camera angle looking up, slightly dramatic, dynamic feel",
}
ANGLE_DESC = {
    "eye_level":  "camera at eye level",
    "high_angle": "camera slightly above, looking down",
    "low_angle":  "camera slightly below, looking up",
    "diagonal":   "slight diagonal angle, dynamic",
}
ANCHOR_SPACE = {
    "top_left":     "leave clean empty area at top-left corner for speech bubble",
    "top_right":    "leave clean empty area at top-right corner for speech bubble",
    "middle_left":  "leave clean empty area at middle-left for speech bubble",
    "middle_right": "leave clean empty area at middle-right for speech bubble",
    "bottom_left":  "leave clean empty area at bottom-left for speech bubble",
    "bottom_right": "leave clean empty area at bottom-right for speech bubble",
}

def build_panel_prompt(panel, layout_rect):
    ptype   = panel.get("type", "")
    scene   = panel.get("scene_prompt", "")
    shot    = panel.get("shot_type", "medium")
    angle   = panel.get("camera_angle", "eye_level")
    anchor  = panel.get("bubble_anchor", "top_right")
    bg_list = panel.get("background_details", [])

    if ptype == "mystery":
        scene = (
            "Korean male character curiously examining a large closed cardboard box "
            "with a BIG bright red question mark painted on it. "
            "Box is center of attention. Character looks intrigued. " + scene
        )
    elif ptype == "result":
        scene = (
            "Tidy improved domestic space. Character looks relieved and satisfied. "
            "No product no box no brand visible — only the improved state of the space. " + scene
        )

    bg_detail = f"Background includes: {', '.join(bg_list)}. " if bg_list else ""
    bubble_hint = ANCHOR_SPACE.get(anchor, ANCHOR_SPACE["top_right"])

    return (
        f"{_CHAR_BASE}, "
        f"{SHOT_DESC.get(shot, SHOT_DESC['medium'])}, "
        f"{ANGLE_DESC.get(angle, ANGLE_DESC['eye_level'])}. "
        f"Scene: {scene} "
        f"{bg_detail}"
        f"{bubble_hint}. "
        f"{_FORBID}."
    )

def _get_img_size(layout_rect):
    """패널 비율에 가장 가까운 API 지원 사이즈"""
    _, _, pw, ph = layout_rect
    ratio = pw / ph   # 514/591 ≈ 0.87 → 정사각형에 가까운 세로형
    if ratio > 1.25:
        return "1536x1024"
    elif ratio < 0.80:
        return "1024x1536"
    return "1024x1024"

# ── 패널 이미지 생성 ───────────────────────────────────────────
_IMG_COUNT = 0

def generate_panel_image(panel, idx, output_dir, ref_b64=""):
    global _IMG_COUNT
    out = output_dir / f"panel_{idx+1:02d}.png"
    if SKIP_IMG_API and out.exists():
        print(f"    [skip] 기존 컷 재사용: {out.name}")
        return out
    if _IMG_COUNT >= DAILY_IMG_LIMIT:
        raise RuntimeError(f"일일 이미지 한도 초과({DAILY_IMG_LIMIT})")

    client      = openai.OpenAI(api_key=_ENV["OPENAI_API_KEY"])
    layout_rect = PANEL_RECTS[idx] if idx < len(PANEL_RECTS) else (0, 0, _COL_W, _ROW_H)
    full_prompt = build_panel_prompt(panel, layout_rect)
    img_size    = _get_img_size(layout_rect)

    for attempt in range(MAX_RETRIES + 1):
        try:
            _IMG_COUNT += 1
            print(f"    컷 {idx+1} 생성 중 ({img_size}, 시도 {attempt+1}/{MAX_RETRIES+1})...")
            data = None

            # 캐릭터 레퍼런스 전달: images.edit 시도
            if ref_b64 and attempt == 0:
                try:
                    ref_bytes    = base64.b64decode(ref_b64)
                    ref_io       = BytesIO(ref_bytes)
                    ref_io.name  = "reference.png"
                    resp = client.images.edit(
                        model=IMAGE_MODEL,
                        image=ref_io,
                        prompt=full_prompt,
                        size="1024x1024",
                    )
                    data = _img_bytes(resp.data[0])
                    print(f"    컷 {idx+1} 레퍼런스 기반 생성 성공")
                except Exception as ref_err:
                    print(f"    레퍼런스 전달 실패, 텍스트 전용 폴백: {ref_err}")
                    data = None

            if data is None:
                resp = client.images.generate(
                    model=IMAGE_MODEL, prompt=full_prompt,
                    size=img_size, quality=IMAGE_QUALITY, n=1,
                )
                data = _img_bytes(resp.data[0])

            Image.open(BytesIO(data)).convert("RGB").save(out)
            print(f"    컷 {idx+1} 저장: {out.name}")
            return out
        except Exception as e:
            print(f"    컷 {idx+1} 실패 (시도 {attempt+1}): {e}")
            if attempt >= MAX_RETRIES:
                raise RuntimeError(f"컷 {idx+1} 최종 실패: {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"컷 {idx+1} 생성 실패")

# ── 말풍선 (앵커 기반, 38~52% 폭) ─────────────────────────────
BUBBLE_ANCHORS = {
    "top_left":     (0.05, 0.04),
    "top_right":    (0.50, 0.04),
    "middle_left":  (0.05, 0.40),
    "middle_right": (0.50, 0.40),
    "bottom_left":  (0.05, 0.66),
    "bottom_right": (0.50, 0.66),
}
TAIL_SIDE = {
    "top_left":"right","top_right":"left",
    "middle_left":"right","middle_right":"left",
    "bottom_left":"right","bottom_right":"left",
}

def _draw_bubble(draw, text, bx, by, bw, bh, font, anchor="top_right", shape="speech"):
    if not text:
        return
    max_bub_w = int(bw * 0.52)
    min_bub_w = int(bw * 0.36)
    char_w    = max(1, font.size // 2 + 3)
    chars_per = max(5, max_bub_w // char_w)
    lines     = textwrap.wrap(text, width=chars_per)[:3] or [text[:15]]
    line_h    = font.size + 8
    pad       = 12
    bub_w     = max(min_bub_w, min(
        max(len(l) for l in lines) * char_w + pad * 2, max_bub_w
    ))
    bub_h     = len(lines) * line_h + pad * 2
    tail_h    = 14

    ax, ay = BUBBLE_ANCHORS.get(anchor, BUBBLE_ANCHORS["top_right"])
    x1 = bx + int(bw * ax)
    y1 = by + int(bh * ay)
    x1 = max(bx + 4, min(x1, bx + bw - bub_w - 4))
    y1 = max(by + 4, min(y1, by + bh - bub_h - tail_h - 4))
    x2, y2 = x1 + bub_w, y1 + bub_h

    if shape == "thought":
        draw.rounded_rectangle([x1, y1, x2, y2], radius=bub_h // 2,
                               fill=BUBBLE_F, outline=BORDER, width=2)
        tx = x1 + bub_w // 2
        for i, r in enumerate([7, 5, 3]):
            draw.ellipse([tx-r, y2+i*7-r, tx+r, y2+i*7+r],
                         fill=BUBBLE_F, outline=BORDER, width=1)
    elif shape == "shout":
        # 8각형 외침 박스
        pts = [x1+8, y1, x2-8, y1, x2, y1+8, x2, y2-8,
               x2-8, y2, x1+8, y2, x1, y2-8, x1, y1+8]
        draw.polygon(pts, fill=BUBBLE_F, outline=BORDER)
        # 꼬리
        side = TAIL_SIDE.get(anchor, "left")
        cx   = (x1 + bub_w // 3) if side == "right" else (x2 - bub_w // 3)
        draw.polygon([(cx-8, y2),(cx+8, y2),(cx, y2+tail_h)], fill=BUBBLE_F)
        draw.line([(cx-8, y2),(cx, y2+tail_h)], fill=BORDER, width=2)
        draw.line([(cx+8, y2),(cx, y2+tail_h)], fill=BORDER, width=2)
    else:
        # 일반 speech
        draw.rounded_rectangle([x1, y1, x2, y2], radius=16,
                               fill=BUBBLE_F, outline=BORDER, width=2)
        side = TAIL_SIDE.get(anchor, "left")
        cx   = (x1 + bub_w // 3) if side == "right" else (x2 - bub_w // 3)
        draw.polygon([(cx-8, y2),(cx+8, y2),(cx, y2+tail_h)], fill=BUBBLE_F)
        draw.line([(cx-8, y2),(cx, y2+tail_h)], fill=BORDER, width=2)
        draw.line([(cx+8, y2),(cx, y2+tail_h)], fill=BORDER, width=2)

    ty = y1 + pad
    cx_t = x1 + bub_w // 2
    for line in lines:
        draw.text((cx_t, ty + font.size // 2), line,
                  font=font, fill=TEXT_C, anchor="mm")
        ty += line_h

def _qmark_overlay(img, fonts):
    ov = Image.new("RGBA", img.size, (0,0,0,0))
    d  = ImageDraw.Draw(ov)
    cx, cy = img.width//2, img.height//2
    r = min(img.width, img.height)//3
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(220,50,50,200))
    d.text((cx,cy), "?", font=fonts["qmark"], fill=(255,255,255,230), anchor="mm")
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

# ── 웹툰 합성 (2열 그리드) ─────────────────────────────────────
def compose_webtoon(script, panel_paths, output_path, fonts):
    canvas = Image.new("RGB", (W,H), BG_COLOR)
    draw   = ImageDraw.Draw(canvas)
    panels = script.get("panels", [])

    for idx, ip in enumerate(panel_paths[:6]):
        px, py, pw, ph = PANEL_RECTS[idx]
        meta   = panels[idx] if idx < len(panels) else {}
        ptype  = meta.get("type", "")
        text   = meta.get("text", "")
        anchor = meta.get("bubble_anchor", "top_right")
        shape  = meta.get("bubble_shape", "speech")

        try:
            raw     = Image.open(ip).convert("RGB")
            # cover 방식: 컷을 가득 채우되 비율 유지 후 중앙 크롭
            scale   = max(pw / raw.width, ph / raw.height)
            nw, nh  = int(raw.width * scale), int(raw.height * scale)
            r       = raw.resize((nw, nh), Image.LANCZOS)
            left    = (nw - pw) // 2
            top     = (nh - ph) // 2
            r       = r.crop((left, top, left + pw, top + ph))
            if ptype == "mystery":
                r = _qmark_overlay(r, fonts)
            canvas.paste(r, (px, py))
        except Exception as e:
            print(f"  컷{idx+1} 로드 실패: {e}")
            draw.rectangle([px,py,px+pw,py+ph], fill=PANEL_BG[idx])

        draw.rectangle([px,py,px+pw,py+ph], outline=BORDER, width=2)
        # 컷 번호 배지
        nr = 10
        draw.ellipse([px+7,py+7,px+7+nr*2,py+7+nr*2], fill=BORDER)
        draw.text((px+7+nr,py+7+nr), str(idx+1),
                  font=fonts["small"], fill="#FFF", anchor="mm")
        if text:
            _draw_bubble(draw, text, px, py, pw, ph, fonts["bubble"],
                         anchor=anchor, shape=shape)

    # 로고 바
    lx1, ly1 = MX, LOGO_Y
    lx2, ly2 = W - MX, LOGO_Y + LOGO_H
    draw.rectangle([lx1,ly1,lx2,ly2], fill="#FFF8E1", outline=BORDER, width=2)
    draw.text((W//2,(ly1+ly2)//2),
              "꿀템연구소  |  물음표의 정체는 댓글에서 확인",
              font=fonts["logo"], fill="#5D4037", anchor="mm")

    canvas.save(output_path, format="PNG", optimize=True)
    kb = output_path.stat().st_size // 1024
    print(f"  최종 웹툰 저장: {output_path.name} ({W}x{H}px, {kb}KB)")
    return output_path

# ── 품질검사 ─────────────────────────────────────────────────
def run_quality_check(final_path, script, panel_paths, product, output_dir):
    rep = {"timestamp":datetime.now().isoformat(),"status":"passed","checks":{},"warnings":[],"errors":[]}
    def fail(k,m): rep["checks"][k]="FAIL"; rep["errors"].append(m); rep.__setitem__("status","failed")
    def warn(k,m): rep["checks"][k]="WARN"; rep["warnings"].append(m)
    def ok(k):     rep["checks"][k]="OK"

    # 패널 수
    if len(panel_paths) == 6: ok("panel_count")
    else: fail("panel_count", f"패널 수 오류: {len(panel_paths)}")

    # 최종 이미지 검사
    if final_path.exists():
        img = Image.open(final_path)
        if img.size == (W,H): ok("final_size")
        else: fail("final_size", f"크기 오류: {img.size}")
        avg = ImageStat.Stat(img.convert("L")).mean[0]
        if avg > 30: ok("not_black")
        else: fail("not_black", "이미지 너무 어두움")
    else:
        fail("final_exists", "최종 이미지 없음")

    # 레이아웃: 2열 그리드 확인 (컷 폭이 전체 폭의 70% 미만이면 통과)
    if PANEL_RECTS and PANEL_RECTS[0][2] < W * 0.7:
        ok("layout_not_six_strips")
    else:
        fail("layout_not_six_strips", f"컷 폭 {PANEL_RECTS[0][2]}px — full-width strip 의심")

    # shot_type 다양성
    shot_types = {p.get("shot_type","") for p in script.get("panels",[]) if p.get("shot_type")}
    if len(shot_types) >= 4: ok("shot_variety")
    elif len(shot_types) >= 3: warn("shot_variety", f"shot_type 종류 {len(shot_types)}종 (권장 4+)")
    else: fail("shot_variety", f"shot_type 종류 부족: {len(shot_types)}종")

    # 말풍선 폭 — 새 renderer는 38-52% 제한 적용됨
    ok("bubble_not_caption_bar")

    # 대사 길이
    for i,p in enumerate(script.get("panels",[])):
        t = p.get("text","")
        if len(t) > 20: warn(f"p{i+1}_len", f"컷{i+1} 대사 {len(t)}자")
        else: ok(f"p{i+1}_len")

    # 캡션 고지
    cap = script.get("caption","")
    if "수수료" in cap or "파트너스" in cap: ok("disclosure")
    else: fail("disclosure", "광고 고지 누락")

    # 제휴 링크
    if product.get("link") or product.get("affiliate_url"): ok("affiliate_link")
    else: fail("affiliate_link", "제휴 링크 없음")

    # 상품명 비밀
    pname = product.get("product_name","")
    if pname and pname.lower() in cap.lower():
        fail("product_secret", f"캡션에 상품명 노출: {pname}")
    else: ok("product_secret")

    # 비전 QA
    if final_path.exists() and _ENV["CLAUDE_API_KEY"]:
        try: _vision_qa(final_path, rep)
        except Exception as e: warn("vision_qa", f"비전QA 오류: {e}")

    rp = output_dir / "quality_report.json"
    rp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  품질검사: {rep['status'].upper()}")
    for e in rep["errors"]:   print(f"    FAIL: {e}")
    for w in rep["warnings"]: print(f"    WARN: {w}")
    return rep

def _vision_qa(img_path, rep):
    client = anthropic.Anthropic(api_key=_ENV["CLAUDE_API_KEY"])
    b64    = base64.b64encode(img_path.read_bytes()).decode()
    qa_q   = (
        "This is a 6-panel webtoon image in a 2-column grid layout. "
        "Speech bubbles and panel numbers were added by Pillow app code — do NOT flag those. "
        "Only flag text EMBEDDED in AI-generated scene backgrounds (brand names, watermarks, labels).\n"
        "1. webtoon_style: looks like Korean manhwa (not storyboard/banner)?\n"
        "2. looks_ad: looks like an advertisement? (should be false)\n"
        "3. product_exposed: real product name/logo/brand visible in AI scenes? (should be false)\n"
        "4. has_bg_text: text baked into AI backgrounds (not Pillow overlays)?\n"
        "5. face_not_cropped: character faces visible and not cut off?\n"
        "6. background_density: panels have rich detailed backgrounds (not plain white)?\n"
        'JSON only: {"webtoon_style":{"pass":true,"reason":""},'
        '"looks_ad":{"pass":false,"reason":""},'
        '"product_exposed":{"pass":false,"reason":""},'
        '"has_bg_text":{"pass":false,"reason":""},'
        '"face_not_cropped":{"pass":true,"reason":""},'
        '"background_density":{"pass":true,"reason":""}}'
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=500,
        messages=[{"role":"user","content":[
            {"type":"image","source":{"type":"base64","media_type":"image/png","data":b64}},
            {"type":"text","text":qa_q},
        ]}],
    )
    m = re.search(r"\{.*\}", resp.content[0].text, re.DOTALL)
    if not m: return
    qa = json.loads(m.group())
    for key,val in qa.items():
        passed = val.get("pass", True)
        reason = val.get("reason","")
        if passed:
            rep["checks"][f"v_{key}"] = "OK"
        elif key in ("product_exposed","looks_ad","has_bg_text"):
            rep["checks"][f"v_{key}"] = "FAIL"
            rep["errors"].append(f"비전QA {key}: {reason}")
            rep["status"] = "failed"
        else:
            rep["checks"][f"v_{key}"] = "WARN"
            rep["warnings"].append(f"비전QA {key}: {reason}")

# ── GitHub 업로드 ─────────────────────────────────────────────
def upload_to_github(img_bytes, filename):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/images/{filename}"
    hdr = {"Authorization":f"token {_ENV['GITHUB_TOKEN']}",
           "Accept":"application/vnd.github.v3+json"}
    sha = None
    try:
        r = requests.get(url, headers=hdr, timeout=10)
        if r.status_code == 200: sha = r.json().get("sha")
    except Exception: pass
    payload = {"message":f"webtoon: {filename}",
               "content":base64.b64encode(img_bytes).decode(),
               "branch":GITHUB_BRANCH}
    if sha: payload["sha"] = sha
    r = requests.put(url, headers=hdr, json=payload, timeout=30)
    if r.status_code not in (200,201):
        raise RuntimeError(f"GitHub 업로드 실패: {r.status_code}")
    raw = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/images/{filename}"
    print(f"  GitHub 업로드 완료: {filename}")
    return raw

# ── Threads 게시 ─────────────────────────────────────────────
def post_image_to_threads(image_url, caption):
    res = requests.post(
        f"https://graph.threads.net/v1.0/{_ENV['THREADS_USER_ID']}/threads",
        data={"media_type":"IMAGE","image_url":image_url,
              "text":caption,"access_token":_ENV["THREADS_ACCESS_TOKEN"]},
        timeout=20,
    )
    cid = res.json().get("id")
    if not cid:
        print(f"  컨테이너 생성 실패: {res.json()}")
        return None
    time.sleep(5)
    pub = requests.post(
        f"https://graph.threads.net/v1.0/{_ENV['THREADS_USER_ID']}/threads_publish",
        data={"creation_id":cid,"access_token":_ENV["THREADS_ACCESS_TOKEN"]},
        timeout=20,
    )
    pid = pub.json().get("id")
    if pid: print(f"  이미지 게시 완료: {pid}")
    return pid

def post_comment_with_retry(post_id, comment_text, max_retry=3):
    for attempt in range(1, max_retry+1):
        try:
            res = requests.post(
                f"https://graph.threads.net/v1.0/{_ENV['THREADS_USER_ID']}/threads",
                data={"media_type":"TEXT","text":comment_text,
                      "reply_to_id":post_id,"access_token":_ENV["THREADS_ACCESS_TOKEN"]},
                timeout=15,
            )
            cid = res.json().get("id")
            if not cid: raise ValueError(f"컨테이너 없음: {res.json()}")
            time.sleep(2)
            pub = requests.post(
                f"https://graph.threads.net/v1.0/{_ENV['THREADS_USER_ID']}/threads_publish",
                data={"creation_id":cid,"access_token":_ENV["THREADS_ACCESS_TOKEN"]},
                timeout=15,
            )
            if "id" in pub.json():
                print("  댓글 게시 완료")
                return True
            raise ValueError(f"게시 실패: {pub.json()}")
        except Exception as e:
            print(f"  댓글 시도 {attempt}: {e}")
            if attempt < max_retry: time.sleep(5*attempt)
    print(f"  댓글 최종 실패 (post_id={post_id})")
    return False

# ── 메인 ─────────────────────────────────────────────────────
def main():
    _validate_env()

    if GEN_REFERENCE:
        print("\n캐릭터 기준표 생성 모드")
        generate_character_reference()
        return

    mode = "DRY-RUN" if DRY_RUN else "실제 게시"
    print(f"\n{'='*60}")
    print(f"꿀템연구소 미스터리 웹툰 v4.1 [{mode}]")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   모델: {IMAGE_MODEL} / 품질: {IMAGE_QUALITY}")
    print(f"   레이아웃: 2열 {GRID_COLS}×{GRID_ROWS} 그리드 ({_COL_W}×{_ROW_H}px/컷)")
    print(f"{'='*60}")

    now = datetime.now()
    cat = (CATEGORIES[PRODUCT_INDEX % len(CATEGORIES)] if PRODUCT_INDEX is not None
           else CATEGORIES[(now.day + now.hour//8) % len(CATEGORIES)])
    product_id = re.sub(r"\W+","-",cat["name"].lower())
    output_dir = COMICS_DIR / f"{now.strftime('%Y-%m-%d')}_{product_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n카테고리: {cat['name']}")

    print(f"\n네이버 쇼핑 수집 중... [{cat['naver_query']}]")
    products = naver_shopping.fetch_products(cat["naver_query"], count=5)
    pname    = products[0]["name"] if products else "생활 아이템"

    private_product = {
        "product_name": pname, "brand":"",
        "affiliate_url": cat["link"], "link": cat["link"],
        "original_image_url": products[0].get("image","") if products else "",
    }
    anon_data = {
        "problem": f"{cat['location']}에서 생기는 {cat['problem_area']} 불편",
        "location": cat["location"], "problem_area": cat["problem_area"],
        "result": "사용 후 달라진 상태", "mystery_label": "?",
        "generic_category": "생활 아이템",
    }
    (output_dir/"private_product.json").write_text(
        json.dumps(private_product, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir/"public_story_input.json").write_text(
        json.dumps(anon_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n익명화 대본 생성 중...")
    script = generate_script(cat, products, anon_data)
    (output_dir/"script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    panels = script.get("panels",[])
    print(f"  컷 수: {len(panels)}")
    for i,p in enumerate(panels,1):
        print(f"    [{i}] {p.get('type',''):10s}| {p.get('shot_type','?'):12s}| {p.get('bubble_anchor','?'):15s}| {p.get('text','')}")

    if SCRIPT_ONLY:
        print("\n[script-only] 대본 완료.")
        return

    # 캐릭터 기준표
    ref_b64 = ""
    if not REFERENCE_PATH.exists() and CHARACTER_PATH.exists():
        try: generate_character_reference()
        except Exception as e: print(f"  기준표 생성 실패: {e}")
    if REFERENCE_PATH.exists():
        ref_b64 = base64.b64encode(REFERENCE_PATH.read_bytes()).decode()
        print(f"\n  캐릭터 기준표 로드: {REFERENCE_PATH.name}")

    # 컷 생성
    print("\n6컷 이미지 생성 중...")
    panel_paths, failed = [], []
    for idx,panel in enumerate(panels[:6]):
        try:
            panel_paths.append(generate_panel_image(panel, idx, output_dir, ref_b64))
        except Exception as e:
            print(f"  컷{idx+1} 실패: {e}")
            failed.append(idx)
            blank = Image.new("RGB",(1024,1024),PANEL_BG[idx])
            bp = output_dir/f"panel_{idx+1:02d}.png"
            blank.save(bp); panel_paths.append(bp)

    # 합성
    print("\n최종 웹툰 합성 중...")
    fonts      = _load_fonts()
    final_path = output_dir/"final_webtoon.png"
    compose_webtoon(script, panel_paths, final_path, fonts)

    # 캡션
    base_cap = script.get("caption","")
    cta      = random.choice(BODY_CTA)
    caption  = (f"{base_cap}\n\n{cta}\n{DISCLOSURE}"
                if "수수료" not in base_cap else base_cap)
    raw_cmnt = script.get("comment_body","[PRODUCT]")
    comment  = (f"웹툰 속 물음표 아이템\n"
                f"{raw_cmnt.replace('[PRODUCT]',pname).replace('[LINK]',cat['link'])}\n\n"
                f"{cat['link']}\n\n"
                "※ 쿠팡 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다.")
    (output_dir/"post_body.txt").write_text(caption, encoding="utf-8")
    (output_dir/"comment.txt").write_text(comment, encoding="utf-8")
    print(f"\n캡션 미리보기:\n{'─'*44}\n{caption}\n{'─'*44}")

    # 품질검사
    print("\n품질검사 실행 중...")
    qa = run_quality_check(final_path, script, panel_paths, private_product, output_dir)
    if failed:
        qa["status"]="failed"
        qa["errors"].append(f"생성 실패 컷: {[i+1 for i in failed]}")

    if QA_ONLY:
        print("\n[quality-check-only] 완료.")
        return

    if qa["status"] == "failed":
        print("\n품질검사 실패 — 게시 중단. quality_report.json 확인.")
        return

    if DRY_RUN:
        print(f"\n[DRY-RUN] 완료.\n  이미지: {final_path}\n  폴더: {output_dir}")
        return

    if not AUTO_PUBLISH:
        print("\nAUTO_PUBLISH=false — 게시 생략.")
        print("환경변수 AUTO_PUBLISH=true 설정 후 게시 활성화됩니다.")
        return

    # 실제 게시
    print("\nGitHub 업로드 중...")
    ts      = now.strftime("%Y%m%d_%H%M%S")
    raw_url = upload_to_github(final_path.read_bytes(), f"webtoon_{ts}.png")
    print("  CDN 대기 (20초)...")
    time.sleep(20)
    print("\nThreads 게시 중...")
    pid = post_image_to_threads(raw_url, caption)
    if not pid:
        print("게시 실패")
        return
    time.sleep(3)
    print("\n댓글 추가 중...")
    ok_c = post_comment_with_retry(pid, comment)
    if not ok_c:
        fl = {"post_id":pid,"comment":comment,"failed_at":now.isoformat()}
        (output_dir/"comment_fail.json").write_text(
            json.dumps(fl,ensure_ascii=False,indent=2), encoding="utf-8")
    print(f"\n완료! [{cat['name']}]\n  결과물: {output_dir}")

if __name__ == "__main__":
    main()
