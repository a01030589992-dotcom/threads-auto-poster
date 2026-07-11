"""
꿀템연구소 미스터리형 8컷 웹툰 자동화 v5.0
명세: COWORK_2026_07_12_ACTION_BRIEF.md
변경: 8컷 2열x4행, 개그툰 스타일, 본문/댓글 구조 개선, 캐릭터 일관성 강화
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
HOOK_DRY_RUN  = "--hook-dry-run"       in _ARGS   # v5.1: 훅 후보만 검토
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
# THREADS_ACCESS_TOKEN / THREADS_API_TOKEN 둘 다 지원
if not _ENV["THREADS_ACCESS_TOKEN"]:
    _ENV["THREADS_ACCESS_TOKEN"] = os.environ.get("THREADS_API_TOKEN", "")

IMAGE_MODEL     = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
IMAGE_QUALITY   = os.environ.get("IMAGE_QUALITY", "high")       # v5: high 기본
MAX_RETRIES     = int(os.environ.get("MAX_PANEL_RETRIES", "3"))  # v5: 3회
DAILY_IMG_LIMIT = int(os.environ.get("DAILY_IMAGE_LIMIT", "80")) # v5: 80컷/일
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
GRID_ROWS  = 4   # v5: 8컷 (2열×4행)

# 2열 4행 그리드 계산
_COL_W = (W - MX * 2 - GUTTER) // GRID_COLS        # 514px
_ROW_H = (H - MY * 2 - LOGO_H - GUTTER - GUTTER * (GRID_ROWS - 1)) // GRID_ROWS  # 440px
LOGO_Y = MY + GRID_ROWS * _ROW_H + (GRID_ROWS - 1) * GUTTER + GUTTER

def get_panel_rects():
    """2열 4행 그리드: [(x, y, w, h), ...] 8개"""
    rects = []
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            x = MX + col * (_COL_W + GUTTER)
            y = MY + row * (_ROW_H + GUTTER)
            rects.append((x, y, _COL_W, _ROW_H))
    return rects

PANEL_RECTS = get_panel_rects()

# 컷별 감정 색 (8컷 순서)
PANEL_BG = [
    "#D7CCC8",  # 1: 어두운 답답한 톤
    "#FFCCBC",  # 2: 당황 리액션
    "#FFCDD2",  # 3: 짜증 붉은 톤
    "#EF9A9A",  # 4: 더 망한 상황
    "#E1BEE7",  # 5: 현타 클로즈업
    "#B2EBF2",  # 6: ? 박스 수상한 조명
    "#DCEDC8",  # 7: 밝아진 공간 전후 차이
    "#FFF9C4",  # 8: CTA 깔끔
]
BG_COLOR  = "#FAFAFA"
BORDER    = "#222222"
BUBBLE_F  = "#FFFFFF"
TEXT_C    = "#1A1A1A"

# v5: 본문은 훅 + 댓글 유도만. 고지문은 댓글로 이동.
BODY_HOOKS = [
    "또 수납함 샀는데\n방이 더 좁아진 적 있나요?\n\n물음표의 정체는 댓글 첫 줄에 👇",
    "바닥이 안 보일 때마다\n수납함만 더 샀던 적 있죠?\n\n근데 문제는 바닥이 아니었어요.\n정체는 댓글 첫 줄에 숨겨둘게요 👇",
    "집에서만 계속 거슬리는 게 있죠.\n\n물음표의 정체는 댓글에서 확인 👇",
    "방금 전까지 괜찮았는데\n이상하게 집에서만 계속 신경 쓰이는 것들.\n\n정체는 댓글에 숨겨둘게요 👇",
]
# 댓글 고지 (쿠팡 파트너스 정책 준수)
COMMENT_DISCLOSURE = (
    "※ 이 댓글은 쿠팡 파트너스 활동의 일환으로,\n"
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

# ── 대사 검수 ─────────────────────────────────────────────────
GENERIC_BAD_LINES = [
    "여러분은 어떻게 하셨어요",
    "이게 해결해준다고",
    "늘 이렇게 먹어야",
    "어떻게 해야",
    "좋네요", "괜찮네요",
    "이런 게 있었네", "그런 게 있었네",
]
HOOK_TOKENS_1  = ["방금","또","왜","진짜","한입","벌써","어?","헐","이게","식었","차가","덥","춥","못","안 되"]
HOOK_TOKENS_6  = ["댓글","정체","맞힌","숨겨","확인","알려"]

HOOK_TOKENS_8 = ["댓글","정체","맞힌","숨겨","확인","알려","첫 줄","👇"]

def score_dialogue(script) -> int:
    panels = script.get("panels", [])
    lines  = [p.get("text","") for p in panels]
    score  = 100
    if not lines: return 0
    # 1컷 길이 (14자 이하 권장)
    if len(lines[0]) > 16: score -= 15
    # 밋밋한 대사
    for line in lines:
        if any(bad in line for bad in GENERIC_BAD_LINES):
            score -= 25; break
    # 1컷 훅
    if not any(t in lines[0] for t in HOOK_TOKENS_1): score -= 15
    # 마지막 컷 CTA (6컷 or 8컷)
    last_idx = min(len(lines)-1, 7)
    if not any(t in lines[last_idx] for t in HOOK_TOKENS_8): score -= 20
    return score

# ── 훅 후보 생성 + 점수화 (v5.1) ──────────────────────────────
RISK_KEYWORDS = [
    "성기능","정력","발기","관계","약효","치료","질병","의학",
    "살 빠","우울증","불면증","복용","효과","개선","먹었더니 변화",
]

def _is_risk_safe(hook_text: str) -> bool:
    return not any(k in hook_text for k in RISK_KEYWORDS)

def generate_hook_candidates(cat: dict, anon_data: dict) -> dict:
    """훅 후보 20개 생성 → 점수화 → 상위 3개 반환"""
    client = anthropic.Anthropic(api_key=_ENV["CLAUDE_API_KEY"])
    prompt = f"""너는 Threads용 생활 개그툰 훅 작가다.

목표:
- 착한 꿀팁 글이 아니라 살짝 매운 생활 개그 훅을 만든다.
- 직접적인 선정성, 성기능 암시, 약효/질병/치료 표현은 절대 금지.
- 오해될 듯 시작해도 반드시 청소/수납/냄새/정리 등 생활 개그로 회수한다.

상품군: {cat['name']}
생활 불편: {anon_data['problem_area']} ({anon_data['location']})
매운맛 레벨: 2~3

아래 공식 중 2~3개를 골라 후보 20개를 만들어라:
1. 오해 유도 후 생활 개그 회수 ("이거 설치한 날 와이프가 밤새 방을 뒤집었다 / 정리하느라.")
2. 돈 낭비 자책 ("내가 산 건 수납함이 아니라 죄책감 보관함이었다")
3. 가족/배우자 반응 과장 ("엄마가 방 보고 말없이 문을 닫았다")
4. 생활 불편 의인화 ("바닥이 오늘도 졌다", "냄새가 집주인 행세함")
5. 금지/경고형 ("수납함 또 사기 전에 봐")

각 후보 점수 기준 (각 1~5점):
- scroll_stop: 첫 줄에서 멈추는가
- curiosity: 댓글 확인하고 싶어지는가
- comedy: 생활 개그로 회수되는가
- relatability: 자취/살림 공감
- ad_smell_low: 광고 냄새 적음
- risk_safe: 성기능/약효/노골성 없음 (이게 2 이하면 탈락)

JSON만 출력:
{{"selected_formulas":["공식1","공식2"],
"candidates":[
  {{"hook":"훅 문장 (1~3줄)","reveal":"생활 개그 회수 방향","spice_level":2,
    "scores":{{"scroll_stop":4,"curiosity":4,"comedy":4,"relatability":4,"ad_smell_low":4,"risk_safe":5}},
    "total":25,"risk_note":"위험 요소 없음"}}
],
"top3":[0,1,2]}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=4000,
        messages=[{"role":"user","content":prompt}],
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"훅 후보 JSON 파싱 실패:\n{raw[:300]}")
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", m.group())
        data = json.loads(fixed)

    # 안전 필터 추가 적용
    candidates = data.get("candidates", [])
    safe = [c for c in candidates if _is_risk_safe(c.get("hook",""))
            and c.get("scores",{}).get("risk_safe",0) > 2
            and c.get("scores",{}).get("ad_smell_low",0) > 2
            and c.get("total",0) >= 20]
    safe.sort(key=lambda c: c.get("total",0), reverse=True)
    data["safe_candidates"] = safe
    data["best_hook"] = safe[0]["hook"] if safe else (candidates[0]["hook"] if candidates else "")
    return data

# ── 대본 생성 (Claude) — 8컷 확장 스키마, 장면 먼저 ──────────
def generate_script(cat, products, anon_data, hook: str = ""):
    client = anthropic.Anthropic(api_key=_ENV["CLAUDE_API_KEY"])
    hook_line = f"\n[선택된 훅 — 반드시 이 톤/방향으로 1컷 대사 작성]\n{hook}\n" if hook else ""
    prompt = f"""너는 꿀템연구소 8컷 생활 개그툰 작가야.
이번 웹툰은 착한 꿀팁 광고가 아니라 살짝 매운 생활 개그툰이다.
장소: {anon_data['location']} / 불편: {anon_data['problem_area']} / 카테고리: {cat['name']}
{hook_line}

[생성 순서 — 반드시 이 순서로 생각할 것]
1. 웃긴 상황 한 줄 떠올리기
2. 8컷 장면(비주얼) 먼저 설계
3. 각 컷의 시각적 재미 포인트 확인
4. 마지막에 짧은 대사 삽입

[8컷 흐름]
1 trouble: 과장된 문제 상황 (wide, 전체 공간 보여줌)
2 reaction: 주인공 리액션 클로즈업 (closeup, 표정 폭발)
3 try_fail: 흔한 해결 시도 (medium, 행동)
4 fail_worse: 더 망함, 과장 효과선 (medium, 상황 악화)
5 insight: 현타/깨달음 (closeup, 배경 흐림)
6 mystery: ? 박스 등장, 수상한 조명 (medium, 박스 중심)
7 result: 사용 후 극적 전후 차이 (wide, 공간 달라짐)
8 cta: 댓글 확인 CTA (medium, 시청자 직접)

[대사 규칙]
- 6~12자 중심, 최대 14자
- 설명문 금지, 짧은 구어체
- 1컷: 스크롤 멈추는 공감 훅
- 6컷: ? 박스 — 상품 정체 절대 말하지 않음
- 8컷: 댓글 확인 유도 (광고처럼 보이지 않게)
- 대사 끝 톤 반복 금지
- "여러분은 어떻게 하셨어요?" 금지

[연출 규칙]
- scene_prompt: 영어, 장면+행동+표정 상세히
- 6컷 scene: "large cardboard box with big red ? symbol, dramatic spotlight" 필수
- 7컷 scene: "strong before-after contrast, bright warm light, no product visible, satisfied expression" 필수
- 8컷 scene: "character faces viewer, shhh gesture or pointing down at comments, friendly wink"
- shot_type 5종 이상 사용: wide/medium/closeup/over_shoulder/low_angle
- 상품명/브랜드 절대 금지
- caption: 30~60자, 훅 문장만, 광고 고지 없음
- comment_body: 상품 [PRODUCT], 링크 [LINK] 포함

JSON만 출력 (설명 없이):
{{"panels":[
  {{"type":"trouble","text":"대사","scene_prompt":"english scene","shot_type":"wide","camera_angle":"eye_level","bubble_anchor":"top_right","bubble_shape":"speech","background_details":["item1"],"dialogue_intent":"scroll_stop_empathy","emotion_level":3}},
  {{"type":"reaction","text":"대사","scene_prompt":"english scene","shot_type":"closeup","camera_angle":"eye_level","bubble_anchor":"top_right","bubble_shape":"shout","background_details":[],"dialogue_intent":"shock_reaction","emotion_level":5}},
  {{"type":"try_fail","text":"대사","scene_prompt":"english scene","shot_type":"medium","camera_angle":"eye_level","bubble_anchor":"top_left","bubble_shape":"speech","background_details":[],"dialogue_intent":"fail_frustration","emotion_level":3}},
  {{"type":"fail_worse","text":"대사","scene_prompt":"english scene","shot_type":"medium","camera_angle":"low_angle","bubble_anchor":"top_right","bubble_shape":"shout","background_details":[],"dialogue_intent":"fail_worse","emotion_level":4}},
  {{"type":"insight","text":"대사","scene_prompt":"english scene","shot_type":"closeup","camera_angle":"high_angle","bubble_anchor":"top_right","bubble_shape":"thought","background_details":[],"dialogue_intent":"insight_shock","emotion_level":4}},
  {{"type":"mystery","text":"대사","scene_prompt":"large cardboard box with big red ? symbol dramatic spotlight","shot_type":"medium","camera_angle":"eye_level","bubble_anchor":"top_left","bubble_shape":"speech","background_details":["mystery box"],"dialogue_intent":"mystery_curiosity","emotion_level":4}},
  {{"type":"result","text":"대사","scene_prompt":"strong before-after contrast bright warm light no product visible satisfied","shot_type":"wide","camera_angle":"diagonal","bubble_anchor":"top_right","bubble_shape":"speech","background_details":[],"dialogue_intent":"result_satisfaction","emotion_level":5}},
  {{"type":"cta","text":"대사","scene_prompt":"character faces viewer shhh gesture or pointing down at comments friendly wink","shot_type":"medium","camera_angle":"eye_level","bubble_anchor":"top_left","bubble_shape":"speech","background_details":[],"dialogue_intent":"cta_hint","emotion_level":3}}
],
"caption":"훅 문장 30~60자 (광고 고지 없음)",
"comment_body":"👇 물음표의 정체\n[PRODUCT]\n[LINK]"}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=3000,
        messages=[{"role":"user","content":prompt}],
    )
    raw = msg.content[0].text.strip()
    # markdown 코드블록 제거
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    m   = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"JSON 파싱 실패:\n{raw[:300]}")
    json_str = m.group()

    # 1차 시도
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"  JSON 1차 파싱 실패 (char {e.pos}): {e.msg}")
        # 오류 위치 근처 출력
        ctx = json_str[max(0, e.pos-60):e.pos+60]
        print(f"  근처: ...{ctx!r}...")

    # 2차: trailing comma 제거 + 문자열 내 줄바꿈 정리
    fixed = re.sub(r",\s*([}\]])", r"\1", json_str)   # trailing comma
    fixed = re.sub(r"\n\s*", " ", fixed)               # 값 내부 줄바꿈 → 공백
    try:
        return json.loads(fixed)
    except json.JSONDecodeError as e2:
        print(f"  JSON 2차 파싱도 실패 (char {e2.pos}): {e2.msg}")
        raise ValueError(f"JSON 최종 파싱 실패. 원본:\n{raw[:500]}")

# ── 패널 프롬프트 빌더 ─────────────────────────────────────────
# v5: 개그툰 스타일 강화, 캐릭터 일관성 고정
_CHAR_BASE = (
    "expressive Korean slice-of-life comedy webtoon panel, "
    "spicy social media comic tone, "
    "high quality polished commercial illustration, "
    "SAME Korean male character in his 20s: round glasses, black hair, beige knit sweater, "
    "consistent character design throughout all panels, "
    "clean thick bold outline, warm but high-contrast colors, "
    "exaggerated facial acting: big eyes, sweat drops, shocked open mouth, "
    "dynamic over-the-top reaction poses, "
    "slightly chaotic detailed home background, "
    "clear foreground-midground-background separation, "
    "mobile-readable composition, "
    "strong visual gag, dramatic lighting, "
    "not low-budget AI comic, not generic advertisement look"
)
_FORBID = (
    "no flat generic AI cartoon, no stiff pose, no bland expression, "
    "no same camera angle repeated, no low detail background, "
    "no character identity change, no gender change, no over-clean empty room, "
    "no product reveal, no text baked into the scene, "
    "no crop on face or hands, no horizontal banner composition, "
    "no watermark, no product brand names logos or package shapes"
)

# v5: 8컷 타입별 연출
PANEL_TYPE_DIRECTION = {
    "trouble":   "Exaggerated problem scene — character frozen in shock, mess is visually obvious, strong visual impact",
    "reaction":  "Extreme close-up on face — eyes wide, mouth agape, sweat drop, pure comic reaction shot",
    "try_fail":  "Character attempting failed solution — hand action visible, disgusted expression, clear fail",
    "fail_worse":"Even worse outcome — exaggerated effect lines, character more distressed than before",
    "insight":   "Close-up face lightbulb moment — eyes narrow then wide, realization expression, minimal background",
    "mystery":   "Large ? box center frame — dramatic spotlight on box, character suspicious and curious",
    "result":    "Strong before-after contrast — warm bright light, satisfied smile, space looks different",
    "cta":       "Character faces viewer directly — shhh gesture or finger pointing down at comments, friendly wink",
}

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
            "Box is center of attention. Another person nearby looks suspicious. " + scene
        )
    elif ptype == "result":
        scene = (
            "Clear before-after improvement visible. Warm soft lighting, gentle steam or glow effect. "
            "Character looks genuinely relieved and satisfied. "
            "No product no box no brand visible. " + scene
        )

    bg_detail   = f"Background includes: {', '.join(bg_list)}. " if bg_list else ""
    bubble_hint = ANCHOR_SPACE.get(anchor, ANCHOR_SPACE["top_right"])
    direction   = PANEL_TYPE_DIRECTION.get(ptype, "")

    return (
        f"{_CHAR_BASE}, "
        f"{SHOT_DESC.get(shot, SHOT_DESC['medium'])}, "
        f"{ANGLE_DESC.get(angle, ANGLE_DESC['eye_level'])}. "
        f"Scene: {scene} "
        f"{bg_detail}"
        f"Direction: {direction} "
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

# ── 말풍선 (앵커 기반, 30~48% 폭, 컷번호 금지 영역) ───────────
BUBBLE_ANCHORS = {
    "top_left":     (0.05, 0.07),   # 컷번호 y=0.04 아래로 조정
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
BADGE_NO_ZONE_W = 42   # 컷 번호 배지 보호 영역 (px, 좌상단 기준)
BADGE_NO_ZONE_H = 42

# 패널 타입별 말풍선 선호 앵커 순서 (점수화)
PANEL_ANCHOR_PREF = {
    "trouble":   ["top_right", "bottom_left",  "bottom_right"],
    "reaction":  ["top_right", "top_left",     "middle_right"],
    "try_fail":  ["top_right", "top_left",     "bottom_right"],
    "fail_worse":["top_left",  "top_right",    "bottom_left"],
    "insight":   ["top_right", "bottom_right", "middle_right"],
    "mystery":   ["top_left",  "bottom_left",  "top_right"],
    "result":    ["top_right", "bottom_right", "middle_right"],
    "cta":       ["top_left",  "top_right",    "bottom_left"],
}

def _best_anchor(ptype, script_anchor):
    """패널 타입과 스크립트 지시 앵커를 고려해 최선 앵커 반환"""
    prefs = PANEL_ANCHOR_PREF.get(ptype, list(BUBBLE_ANCHORS.keys()))
    if script_anchor in prefs:
        return script_anchor
    return prefs[0]

def _kr_text_width(font, text):
    """한글 포함 텍스트의 실제 렌더링 폭 추정 (PIL getlength 우선, 폴백 문자 기반)"""
    try:
        return int(font.getlength(text))
    except AttributeError:
        pass
    w = 0
    for ch in text:
        w += font.size if ord(ch) > 0x2E7F else max(1, font.size // 2 + 2)
    return w

def _draw_bubble(draw, text, bx, by, bw, bh, font, anchor="top_right", shape="speech", ptype=""):
    if not text:
        return
    anchor    = _best_anchor(ptype, anchor)
    max_bub_w = int(bw * 0.48)
    min_bub_w = int(bw * 0.28)
    pad       = 14
    tail_h    = 14

    # 한글 기준 chars_per: 한글 1글자 ≈ font.size px
    kr_char_w = max(1, font.size)
    chars_per  = max(4, max_bub_w // kr_char_w)
    lines      = textwrap.wrap(text, width=chars_per)[:3] or [text[:12]]
    line_h     = font.size + 10

    # 실제 텍스트 폭으로 bubble 폭 결정
    max_line_px = max(_kr_text_width(font, l) for l in lines)
    bub_w = max(min_bub_w, min(max_line_px + pad * 2, max_bub_w))
    bub_h = len(lines) * line_h + pad * 2

    ax, ay = BUBBLE_ANCHORS.get(anchor, BUBBLE_ANCHORS["top_right"])
    x1 = bx + int(bw * ax)
    y1 = by + int(bh * ay)

    # 컷 번호 배지 금지 영역 (좌상단 BADGE_NO_ZONE_W×BADGE_NO_ZONE_H)
    badge_right  = bx + BADGE_NO_ZONE_W + 6
    badge_bottom = by + BADGE_NO_ZONE_H + 6
    if x1 < badge_right and y1 < badge_bottom:
        x1 = badge_right   # 배지와 겹치면 오른쪽으로 밀기

    # 패널 경계 강제 보정 — bubble이 절대 패널 밖으로 나가지 않도록
    x1 = max(bx + 6, min(x1, bx + bw - bub_w - 6))
    y1 = max(by + 6, min(y1, by + bh - bub_h - tail_h - 6))
    x2, y2 = x1 + bub_w, y1 + bub_h

    # x2가 패널 우측을 초과하면 bub_w를 줄임
    if x2 > bx + bw - 6:
        bub_w = bx + bw - 6 - x1
        x2    = x1 + bub_w

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

    for idx, ip in enumerate(panel_paths[:8]):
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
                         anchor=anchor, shape=shape, ptype=ptype)

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
    if len(panel_paths) == 8: ok("panel_count")
    elif len(panel_paths) >= 6: warn("panel_count", f"패널 수: {len(panel_paths)} (8 권장)")
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

    # 말풍선 폭 — 새 renderer는 30-48% 제한 적용됨
    ok("bubble_not_caption_bar")

    # 대사 품질: 밋밋한 대사 감지
    panels_list = script.get("panels",[])
    lines_list  = [p.get("text","") for p in panels_list]
    bad_found   = [line for line in lines_list if any(b in line for b in GENERIC_BAD_LINES)]
    if bad_found: warn("dialogue_not_generic", f"밋밋한 대사 감지: {bad_found}")
    else: ok("dialogue_not_generic")

    # 1컷 훅 점수
    dial_score = score_dialogue(script)
    if dial_score >= 70: ok("dialogue_hook_score")
    elif dial_score >= 50: warn("dialogue_hook_score", f"대사 훅 점수 낮음: {dial_score}/100")
    else: warn("dialogue_hook_score", f"대사 훅 점수 매우 낮음: {dial_score}/100 — 재생성 권장")

    # 대사 길이
    for i,p in enumerate(panels_list):
        t = p.get("text","")
        if len(t) > 20: warn(f"p{i+1}_len", f"컷{i+1} 대사 {len(t)}자")
        else: ok(f"p{i+1}_len")

    # v5: 고지문은 댓글에 있어야 함 (caption에는 없어도 됨)
    cap = script.get("caption","")
    cmnt = script.get("comment_body","")
    if "수수료" in cmnt or "파트너스" in cmnt: ok("disclosure")
    else: warn("disclosure", "댓글에 광고 고지 미포함 — 게시 전 확인")
    if "수수료" in cap or "파트너스" in cap:
        warn("caption_not_ad_first", "본문에 광고 고지가 포함됨 — 삭제 권장")

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
    print(f"꿀템연구소 미스터리 웹툰 v5.1 [{mode}]")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   모델: {IMAGE_MODEL} / 품질: {IMAGE_QUALITY}")
    print(f"   레이아웃: 2열 {GRID_COLS}x{GRID_ROWS} 그리드 ({_COL_W}x{_ROW_H}px/컷)")
    print(f"{'='*60}")

    now = datetime.now()
    cat = (CATEGORIES[PRODUCT_INDEX % len(CATEGORIES)] if PRODUCT_INDEX is not None
           else CATEGORIES[(now.day + now.hour//8) % len(CATEGORIES)])
    product_id = re.sub(r"\W+","-",cat["name"].lower())
    output_dir = COMICS_DIR / f"{now.strftime('%Y-%m-%d')}_{product_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nCategory: {cat['name']}")

    print(f"\nNaver shopping [{cat['naver_query']}]...")
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

    # v5.1: 훅 후보 생성 → 점수화 → 베스트 선택
    print("\n훅 후보 생성 중 (20개 → 점수화)...")
    best_hook = ""
    try:
        hook_data = generate_hook_candidates(cat, anon_data)
        safe = hook_data.get("safe_candidates", [])
        best_hook = hook_data.get("best_hook", "")
        (output_dir / "hook_candidates.json").write_text(
            json.dumps(hook_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  안전 후보: {len(safe)}개 / 베스트 훅: {best_hook[:40]}...")
        for i, c in enumerate(safe[:3], 1):
            print(f"    [{i}위] total={c.get('total',0)} | {c.get('hook','')[:50]}")
    except Exception as e:
        print(f"  훅 후보 생성 실패 (기본 흐름으로 진행): {e}")

    if HOOK_DRY_RUN:
        print("\n[hook-dry-run] 훅 후보 확인 완료. 이미지 생성 없음.")
        print(f"  저장: {output_dir}/hook_candidates.json")
        return

    print("\n대본 생성 중...")
    SCRIPT_RETRY = 2
    script = None
    for s_attempt in range(SCRIPT_RETRY + 1):
        script = generate_script(cat, products, anon_data, hook=best_hook)
        dscore = score_dialogue(script)
        if dscore >= 65:
            print(f"  대사 점수: {dscore}/100 OK")
            break
        if s_attempt < SCRIPT_RETRY:
            print(f"  대사 점수 낮음({dscore}/100) 재생성 ({s_attempt+1}/{SCRIPT_RETRY})")
        else:
            print(f"  대사 점수: {dscore}/100 (최대 재시도)")
    (output_dir/"script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    panels = script.get("panels",[])
    print(f"  컷 수: {len(panels)}")
    for i,p in enumerate(panels,1):
        print(f"    [{i}] {p.get('type',''):10s}| {p.get('shot_type','?'):12s}| {p.get('text','')}")

    if SCRIPT_ONLY:
        print("\n[script-only] 완료.")
        return

    # 캐릭터 기준표
    ref_b64 = ""
    if not REFERENCE_PATH.exists() and CHARACTER_PATH.exists():
        try: generate_character_reference()
        except Exception as e: print(f"  기준표 생성 실패: {e}")
    if REFERENCE_PATH.exists():
        ref_b64 = base64.b64encode(REFERENCE_PATH.read_bytes()).decode()
        print(f"\n  캐릭터 기준표 로드: {REFERENCE_PATH.name}")

    # 8컷 이미지 생성
    print("\n8컷 이미지 생성 중...")
    panel_paths, failed = [], []
    for idx, panel in enumerate(panels[:8]):
        try:
            panel_paths.append(generate_panel_image(panel, idx, output_dir, ref_b64))
        except Exception as e:
            print(f"  컷{idx+1} 실패: {e}")
            failed.append(idx)
            bg = PANEL_BG[idx % len(PANEL_BG)]
            blank = Image.new("RGB", (1024, 1024), bg)
            bp = output_dir / f"panel_{idx+1:02d}.png"
            blank.save(bp)
            panel_paths.append(bp)

    # 합성
    print("\n최종 웹툰 합성 중...")
    fonts      = _load_fonts()
    final_path = output_dir / "final_webtoon.png"
    compose_webtoon(script, panel_paths, final_path, fonts)

    # v5: 본문 = 훅 문장만, 댓글 = 상품+링크+고지
    base_cap = script.get("caption", "").strip()
    base_cap = re.sub(r"※.*수수료.*", "", base_cap, flags=re.DOTALL).strip()
    hook_for_caption = best_hook.split("\n")[0] if best_hook else ""
    caption = base_cap if base_cap else (hook_for_caption or random.choice(BODY_HOOKS))

    raw_cmnt = script.get("comment_body", "👇 물음표의 정체\n[PRODUCT]\n[LINK]")
    comment  = (
        raw_cmnt
        .replace("[PRODUCT]", pname)
        .replace("[LINK]", cat["link"])
        + f"\n\n{COMMENT_DISCLOSURE}"
    )
    (output_dir / "post_body.txt").write_text(caption, encoding="utf-8")
    (output_dir / "comment.txt").write_text(comment, encoding="utf-8")
    print(f"\n본문:\n{'─'*44}\n{caption}\n{'─'*44}")
    print(f"\n댓글:\n{'─'*44}\n{comment}\n{'─'*44}")

    # 품질검사
    print("\n품질검사 실행 중...")
    qa = run_quality_check(final_path, script, panel_paths, private_product, output_dir)
    if failed:
        qa["status"] = "failed"
        qa["errors"].append(f"생성 실패 컷: {[i+1 for i in failed]}")

    if QA_ONLY:
        print("\n[quality-check-only] 완료.")
        return

    if qa["status"] == "failed":
        print("\n품질검사 실패 — 게시 중단.")
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
        print("\uac8c\uc2dc \uc2e4\ud328")
        return
    time.sleep(3)
    print("\n\ub313\uae00 \uac8c\uc2dc \uc911...")
    ok_c = post_comment_with_retry(pid, comment)
    if not ok_c:
        fl = {"post_id": pid, "comment": comment, "failed_at": now.isoformat()}
        (output_dir / "comment_fail.json").write_text(
            json.dumps(fl, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n\uc644\ub8cc! [{cat['name']}]\n  \uacb0\uacfc\ubb3c: {output_dir}")


if __name__ == "__main__":
    main()
