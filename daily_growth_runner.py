#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_growth_runner.py — 꿀템연구소 하루 3글 성장 루틴 자동화

NOTE: This project does not automate likes, reposts, follows, or repetitive replies.
      It only prepares human-reviewed engagement suggestions.

Usage:
  python daily_growth_runner.py --plan-only        # 오늘 계획만 생성
  python daily_growth_runner.py --dry-run          # 전체 초안 생성 (게시 없음)
  python daily_growth_runner.py --post-emotional   # 감성글만 게시
  python daily_growth_runner.py --prep-engagement  # 스하리 후보/댓글 초안만 생성
  python daily_growth_runner.py --post-webtoon     # 웹툰 생성 및 게시
  python daily_growth_runner.py --run-daily        # 하루 전체 루틴 실행
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# 출력 즉시 표시
sys.stdout.reconfigure(line_buffering=True)  # type: ignore

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore

try:
    import requests
except ImportError:
    requests = None  # type: ignore

# ── 경로 상수 ──────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
LOG_DIR     = BASE_DIR / "growth_logs"
TMPL_DIR    = BASE_DIR / "templates"
CONFIG_FILE = BASE_DIR / "growth_config.json"

LOG_DIR.mkdir(exist_ok=True)
TMPL_DIR.mkdir(exist_ok=True)

# ── 환경변수 ───────────────────────────────────────────────────────────────
CLAUDE_API_KEY    = os.environ.get("CLAUDE_API_KEY", "")
THREADS_USER_ID   = os.environ.get("THREADS_USER_ID", "")
THREADS_API_TOKEN = os.environ.get("THREADS_API_TOKEN", "")

TODAY = date.today().isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# 설정 로딩
# ══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# 일별 로그 관리
# ══════════════════════════════════════════════════════════════════════════════

def log_path(d: str = TODAY) -> Path:
    return LOG_DIR / f"{d}.json"

def md_path(d: str = TODAY) -> Path:
    return LOG_DIR / f"{d}.md"

def load_log(d: str = TODAY) -> dict:
    p = log_path(d)
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            print(f"  ⚠️  로그 파일 손상 ({d}.json) — 초기화")
    return {
        "date": d,
        "posts": [],
        "engagement_prep": {"candidate_count": 0, "manual_completed_count": 0},
        "notes": ""
    }

def save_log(data: dict, d: str = TODAY):
    with open(log_path(d), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  📋 로그 저장: growth_logs/{d}.json")


# ══════════════════════════════════════════════════════════════════════════════
# 안전 장치
# ══════════════════════════════════════════════════════════════════════════════

def _recent_logs(days: int) -> list[dict]:
    """최근 N일 로그 목록 반환."""
    result = []
    for i in range(1, days + 1):
        d = (date.today() - timedelta(days=i)).isoformat()
        p = log_path(d)
        if p.exists():
            with open(p, encoding="utf-8") as f:
                result.append(json.load(f))
    return result

def check_affiliate_limit(cfg: dict) -> bool:
    """오늘 이미 광고성 글이 1개 게시됐으면 False 반환."""
    log = load_log(TODAY)
    count = sum(1 for p in log.get("posts", []) if p.get("type") == "webtoon_affiliate")
    max_allowed = cfg.get("safety", {}).get("max_affiliate_posts_per_day", 1)
    if count >= max_allowed:
        print(f"  ⚠️  오늘 광고성 글이 이미 {count}개 → 게시 제한 (최대 {max_allowed}개/일)")
        return False
    return True

def check_category_cooldown(category: str, cfg: dict) -> bool:
    """같은 카테고리가 최근 2일 내 사용됐으면 False 반환."""
    cooldown = cfg.get("safety", {}).get("avoid_same_category_days", 2)
    logs = _recent_logs(cooldown)
    for log in logs:
        for post in log.get("posts", []):
            if post.get("product_category", "").strip() == category.strip():
                print(f"  ⚠️  카테고리 '{category}'가 최근 {cooldown}일 내 사용됨 → 건너뜀")
                return False
    return True

def check_duplicate_text(text: str, cfg: dict) -> bool:
    """동일 문장이 최근 7일 내 사용됐으면 False 반환."""
    days = cfg.get("safety", {}).get("duplicate_check_days", 7)
    logs = _recent_logs(days)
    cleaned = text.strip()
    for log in logs:
        for post in log.get("posts", []):
            if post.get("text", "").strip() == cleaned:
                print(f"  ⚠️  동일 감성글이 최근 {days}일 내 사용됨 → 재생성 필요")
                return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# 감성글 생성
# ══════════════════════════════════════════════════════════════════════════════

def _pick_topic(cfg: dict) -> str:
    topics = cfg.get("emotional_post", {}).get("topics_cycle", [
        "집안일의 작은 불편함",
        "정리와 수납의 순간",
        "자취 생활의 솔직한 하루",
        "주방에서 생기는 작은 스트레스",
        "계절이 바뀔 때 생기는 생활 변화",
    ])
    logs = _recent_logs(len(topics))
    used = set()
    for log in logs:
        for p in log.get("posts", []):
            if p.get("type") == "emotional":
                used.add(p.get("topic", ""))
    for t in topics:
        if t not in used:
            return t
    # 모두 사용됐으면 첫 번째로 돌아감
    return topics[0]

def generate_emotional_post(cfg: dict, topic: str | None = None) -> dict | None:
    if not CLAUDE_API_KEY:
        print("  ❌ CLAUDE_API_KEY 환경변수 누락")
        return None
    if anthropic is None:
        print("  ❌ anthropic 패키지 미설치 (pip install anthropic)")
        return None

    topic = topic or _pick_topic(cfg)
    print(f"  📝 감성글 주제: {topic}")

    prompt = f"""너는 Threads 계정 "꿀템연구소"의 감성글 작가다.

계정 정체성:
- 생활 속 작은 불편을 발견한다.
- 과장된 광고가 아니라 조용한 공감을 만든다.
- 살림, 정리, 집, 하루, 습관, 작은 개선을 다룬다.

작성 규칙:
- 2~5줄
- 80~220자
- 상품명, 브랜드명, 링크 금지
- 쿠팡 파트너스 문구 금지
- "여러분", "꿀팁", "대박", "필수템" 남발 금지
- AI처럼 교훈적으로 쓰지 말 것
- 마지막 줄은 여운이 남게 작성

오늘의 소재:
{topic}

JSON으로만 출력 (설명 없이):
{{
  "text": "게시글 본문",
  "intent": "공감/위로/생활관찰 중 하나",
  "risk_note": "광고처럼 보일 위험이 있는지 한 줄로"
}}"""

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = msg.content[0].text.strip()
            # JSON 추출
            m = re.search(r"\{[\s\S]+\}", raw)
            if not m:
                raise ValueError("JSON 없음")
            data = json.loads(m.group(0))
            text = data.get("text", "").strip()
            length = len(text)
            if length < 80:
                print(f"  ⚠️  감성글이 너무 짧음 ({length}자) — 재시도 ({attempt+1}/3)")
                continue
            if length > 220:
                print(f"  ⚠️  감성글이 너무 김 ({length}자) — 재시도 ({attempt+1}/3)")
                continue
            data["topic"] = topic
            data["char_count"] = length
            print(f"  ✅ 감성글 생성 ({length}자): {text[:40]}…")
            return data
        except Exception as e:
            print(f"  ❌ 감성글 생성 실패 (시도 {attempt+1}/3): {e}")
    return None

def _validate_emotional(text: str) -> bool:
    """광고성 표현이 있으면 False."""
    bad_words = ["쿠팡", "파트너스", "링크", "구매", "클릭", "http", "필수템", "대박", "할인", "원가"]
    for w in bad_words:
        if w in text:
            print(f"  ⚠️  감성글에 금지어 '{w}' 포함")
            return False
    return True

def post_emotional_to_threads(text: str, dry_run: bool = False) -> str | None:
    """Threads에 감성글 텍스트 게시. dry_run이면 게시 생략."""
    if dry_run:
        print("  [DRY-RUN] 감성글 게시 생략")
        return "dry-run"

    if not THREADS_USER_ID or not THREADS_API_TOKEN:
        print("  ❌ Threads API 환경변수 누락 (THREADS_USER_ID / THREADS_API_TOKEN)")
        return None
    if requests is None:
        print("  ❌ requests 패키지 미설치 (pip install requests)")
        return None

    base = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}"

    # 1) 컨테이너 생성
    r = requests.post(f"{base}/threads", params={
        "media_type": "TEXT",
        "text": text,
        "access_token": THREADS_API_TOKEN,
    }, timeout=15)
    r.raise_for_status()
    container_id = r.json().get("id")
    if not container_id:
        print(f"  ❌ 컨테이너 생성 실패: {r.text}")
        return None

    import time; time.sleep(3)

    # 2) 게시
    r2 = requests.post(f"{base}/threads_publish", params={
        "creation_id": container_id,
        "access_token": THREADS_API_TOKEN,
    }, timeout=15)
    r2.raise_for_status()
    post_id = r2.json().get("id")
    url = f"https://www.threads.net/post/{post_id}" if post_id else ""
    print(f"  ✅ 감성글 게시 완료: {url}")
    return url


# ══════════════════════════════════════════════════════════════════════════════
# 스하리(스레드 하트/리포스트) 반자동 보조
# ══════════════════════════════════════════════════════════════════════════════

ENGAGEMENT_KEYWORDS = [
    "살림", "자취", "집정리", "생활꿀팁", "주방", "미니멀라이프", "오늘의불편", "집안일"
]

COMMENT_TEMPLATES = [
    "이거 진짜 은근 스트레스죠. 저도 매번 넘겼던 부분이에요.",
    "이런 작은 불편이 쌓이면 집안일이 더 피곤해지는 것 같아요.",
    "공감돼요. 살림은 큰 문제보다 이런 사소한 게 오래 가더라고요.",
    "이렇게 딱 정리해주시니까 맞다 싶어요. 저도 오늘 한 번 돌아보게 됐어요.",
    "작은 거지만 한 번 신경 쓰이면 계속 눈에 밟히더라고요.",
    "집에서 이런 부분 그냥 지나쳤는데 보고 나니까 저도 확인해보게 되네요.",
    "이런 불편 진짜 공감돼요. 그냥 참고 사는 게 더 많은 것 같아요.",
    "살다 보면 이런 작은 것들이 쌓여서 피곤해지는 것 같아요. 잘 봤어요.",
    "맞아요. 생활 속 작은 거 하나 해결되면 왜 이렇게 개운한지.",
    "이거 딱 오늘 있었던 일이에요. 덜 피곤해지려면 결국 이런 부분부터인 것 같아요.",
]

def generate_engagement_prep(cfg: dict) -> dict:
    """스하리 후보 목록 + 댓글 초안 생성 → MD 파일 저장."""
    keywords = cfg.get("engagement", {}).get("keywords", ENGAGEMENT_KEYWORDS)
    count    = cfg.get("engagement", {}).get("candidate_count", 5)

    # 후보 목록 (실제 Threads API 연동 없이 키워드 기반 더미 생성)
    candidates = []
    for i in range(count):
        kw = keywords[i % len(keywords)]
        candidates.append({
            "index": i + 1,
            "keyword": kw,
            "url": f"[Threads 검색: #{kw}]",
            "account": "(직접 찾기)",
            "topic": kw + " 관련 일상 공감글",
            "recommended_action": "하트 + 댓글" if i % 2 == 0 else "하트",
            "comment_draft": COMMENT_TEMPLATES[i % len(COMMENT_TEMPLATES)],
            "completed": False,
        })

    # MD 파일 저장
    md_lines = [f"# {TODAY} 스하리 작업 후보\n",
                "> NOTE: 실제 좋아요·리포스트·댓글은 사용자가 직접 실행합니다.\n"]
    for c in candidates:
        md_lines += [
            f"\n## 후보 {c['index']} (#{c['keyword']})",
            f"- URL: {c['url']}",
            f"- 계정: {c['account']}",
            f"- 주제: {c['topic']}",
            f"- 추천 액션: {c['recommended_action']}",
            f"- 댓글 초안: {c['comment_draft']}",
            f"- 완료 여부: [ ]",
        ]

    md_content = "\n".join(md_lines)
    with open(md_path(TODAY), "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"  📋 스하리 체크리스트 저장: growth_logs/{TODAY}.md")

    return {"candidate_count": count, "candidates": candidates}


# ══════════════════════════════════════════════════════════════════════════════
# 웹툰 게시 연동
# ══════════════════════════════════════════════════════════════════════════════

def run_webtoon(dry_run: bool = False, category: str = "") -> dict:
    """comic_poster.py 서브프로세스 호출."""
    cmd = [sys.executable, str(BASE_DIR / "comic_poster.py")]
    if dry_run:
        cmd.append("--dry-run")
    if category:
        cmd += ["--category", category]

    print(f"  🎨 웹툰 실행: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False, text=True)

    if result.returncode != 0:
        print(f"  ❌ 웹툰 게시 실패 (exit {result.returncode})")
        return {"success": False, "category": category}

    print("  ✅ 웹툰 게시 완료")
    return {"success": True, "category": category, "dry_run": dry_run}


# ══════════════════════════════════════════════════════════════════════════════
# 오늘 계획 출력 (--plan-only)
# ══════════════════════════════════════════════════════════════════════════════

def print_plan(cfg: dict):
    sched = cfg.get("schedule", {})
    emo_t  = sched.get("emotional_post_time", "08:00")
    eng_t  = sched.get("engagement_prep_time", "12:30")
    web_t  = sched.get("webtoon_post_time", "20:30")

    log    = load_log(TODAY)
    done   = {p["type"] for p in log.get("posts", [])}

    lines = [
        f"\n📅 [{TODAY}] 오늘 루틴 계획",
        "─" * 40,
        f"  {emo_t}  감성글 자동 게시        {'✅ 완료' if 'emotional' in done else '⏳ 예정'}",
        f"  {eng_t}  스하리 후보/초안 생성   {'✅ 완료' if 'engagement' in done else '⏳ 예정'}",
        f"  {web_t}  광고성 웹툰 게시        {'✅ 완료' if 'webtoon_affiliate' in done else '⏳ 예정'}",
        "─" * 40,
        f"  광고성 글 오늘 게시: {sum(1 for p in log.get('posts',[]) if p.get('type')=='webtoon_affiliate')}/1",
        "",
    ]
    print("\n".join(lines))


# ══════════════════════════════════════════════════════════════════════════════
# 실행 모드
# ══════════════════════════════════════════════════════════════════════════════

def mode_plan_only(cfg: dict):
    print_plan(cfg)


def mode_dry_run(cfg: dict):
    """전체 초안 생성 — 게시 없음."""
    print("\n🔍 [DRY-RUN] 모든 초안 생성 시작\n")

    log = load_log(TODAY)

    # 1) 감성글 초안
    print("[1/3] 감성글 초안 생성 중…")
    emo = generate_emotional_post(cfg)
    if emo and _validate_emotional(emo["text"]):
        if check_duplicate_text(emo["text"], cfg):
            log["posts"].append({
                "type": "emotional",
                "posted_at": "",
                "text": emo["text"],
                "intent": emo.get("intent", ""),
                "topic": emo.get("topic", ""),
                "url": "dry-run",
                "likes": None, "replies": None, "reposts": None,
            })
            print(f"\n  감성글 본문:\n{emo['text']}\n")
    else:
        print("  ⚠️  감성글 품질 검사 실패 또는 생성 실패\n")

    # 2) 스하리 초안
    print("[2/3] 스하리 후보/댓글 초안 생성 중…")
    eng = generate_engagement_prep(cfg)
    log["engagement_prep"]["candidate_count"] = eng["candidate_count"]

    # 3) 웹툰 계획
    print("[3/3] 웹툰 계획 확인 중…")
    if check_affiliate_limit(cfg):
        print("  ✅ 오늘 광고성 웹툰 게시 가능")
        print("  💡 실제 실행: python daily_growth_runner.py --post-webtoon")
        log["posts"].append({
            "type": "webtoon_affiliate",
            "posted_at": "",
            "url": "dry-run",
            "product_category": "",
            "affiliate_link": "",
            "likes": None, "replies": None, "reposts": None,
        })
    else:
        print("  ⚠️  오늘 광고성 웹툰 게시 불가 (제한 초과)")

    save_log(log)
    print("\n✅ DRY-RUN 완료. 실제 게시 없음.")
    print_plan(cfg)


def mode_post_emotional(cfg: dict):
    """감성글 1개만 게시."""
    print("\n📣 [감성글 게시] 시작\n")

    log = load_log(TODAY)

    emo = generate_emotional_post(cfg)
    if not emo:
        print("  ❌ 감성글 생성 실패")
        return
    if not _validate_emotional(emo["text"]):
        print("  ❌ 감성글 품질 검사 실패")
        return
    if not check_duplicate_text(emo["text"], cfg):
        print("  ❌ 중복 감성글 — 게시 중단")
        return

    url = post_emotional_to_threads(emo["text"], dry_run=False)

    log["posts"].append({
        "type": "emotional",
        "posted_at": datetime.now().strftime("%H:%M"),
        "text": emo["text"],
        "intent": emo.get("intent", ""),
        "topic": emo.get("topic", ""),
        "url": url or "",
        "likes": None, "replies": None, "reposts": None,
    })
    save_log(log)


def mode_prep_engagement(cfg: dict):
    """스하리 후보/댓글 초안만 생성."""
    print("\n📋 [스하리 보조] 시작\n")

    log = load_log(TODAY)
    eng = generate_engagement_prep(cfg)
    log["engagement_prep"]["candidate_count"] = eng["candidate_count"]
    save_log(log)
    print(f"\n✅ 스하리 후보 {eng['candidate_count']}개 생성 완료.")
    print(f"   growth_logs/{TODAY}.md 를 열어 확인하세요.")


def mode_post_webtoon(cfg: dict):
    """웹툰 생성 및 게시."""
    print("\n🎨 [웹툰 게시] 시작\n")

    if not check_affiliate_limit(cfg):
        return

    log     = load_log(TODAY)
    result  = run_webtoon(dry_run=False)

    log["posts"].append({
        "type": "webtoon_affiliate",
        "posted_at": datetime.now().strftime("%H:%M"),
        "url": "",
        "product_category": result.get("category", ""),
        "affiliate_link": "",
        "likes": None, "replies": None, "reposts": None,
    })
    save_log(log)


def mode_run_daily(cfg: dict):
    """하루 전체 루틴. 스하리는 초안까지만."""
    print("\n🚀 [하루 루틴 전체] 시작\n")

    log = load_log(TODAY)

    # ① 감성글
    print("─" * 40)
    print("[1/3] 감성글 게시")
    emo = generate_emotional_post(cfg)
    if emo and _validate_emotional(emo["text"]) and check_duplicate_text(emo["text"], cfg):
        url = post_emotional_to_threads(emo["text"], dry_run=False)
        log["posts"].append({
            "type": "emotional",
            "posted_at": datetime.now().strftime("%H:%M"),
            "text": emo["text"],
            "intent": emo.get("intent", ""),
            "topic": emo.get("topic", ""),
            "url": url or "",
            "likes": None, "replies": None, "reposts": None,
        })
    else:
        print("  ⚠️  감성글 게시 건너뜀")

    # ② 스하리
    print("\n" + "─" * 40)
    print("[2/3] 스하리 후보/댓글 초안 생성")
    eng = generate_engagement_prep(cfg)
    log["engagement_prep"]["candidate_count"] = eng["candidate_count"]

    # ③ 웹툰
    print("\n" + "─" * 40)
    print("[3/3] 광고성 웹툰 게시")
    if check_affiliate_limit(cfg):
        result = run_webtoon(dry_run=False)
        log["posts"].append({
            "type": "webtoon_affiliate",
            "posted_at": datetime.now().strftime("%H:%M"),
            "url": "",
            "product_category": result.get("category", ""),
            "affiliate_link": "",
            "likes": None, "replies": None, "reposts": None,
        })
    else:
        print("  ⚠️  웹툰 게시 건너뜀 (일일 광고 한도 초과)")

    save_log(log)
    print("\n✅ 하루 루틴 완료.")
    print_plan(cfg)


# ══════════════════════════════════════════════════════════════════════════════
# 엔트리포인트
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="꿀템연구소 하루 3글 성장 루틴 자동화"
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--plan-only",       action="store_true", help="오늘 계획만 출력")
    grp.add_argument("--dry-run",         action="store_true", help="전체 초안 생성 (게시 없음)")
    grp.add_argument("--post-emotional",  action="store_true", help="감성글 1개 게시")
    grp.add_argument("--prep-engagement", action="store_true", help="스하리 후보/초안 생성")
    grp.add_argument("--post-webtoon",    action="store_true", help="웹툰 생성 및 게시")
    grp.add_argument("--run-daily",       action="store_true", help="하루 루틴 전체 실행")
    args = parser.parse_args()

    cfg = load_config()

    if args.plan_only:
        mode_plan_only(cfg)
    elif args.dry_run:
        mode_dry_run(cfg)
    elif args.post_emotional:
        mode_post_emotional(cfg)
    elif args.prep_engagement:
        mode_prep_engagement(cfg)
    elif args.post_webtoon:
        mode_post_webtoon(cfg)
    elif args.run_daily:
        mode_run_daily(cfg)


if __name__ == "__main__":
    main()
