"""
스레드 + 쿠팡 파트너스 자동화 포스터 (링크 하드코딩 버전)
------------------------------------------------------------
PC가 꺼져 있어도 GitHub Actions로 실행 가능
필요 패키지: pip install anthropic requests
"""

import os
import time
import requests
from datetime import datetime
import anthropic

# ─────────────────────────────────────────
# 환경 변수 (GitHub Secrets에 등록)
# ─────────────────────────────────────────
CLAUDE_API_KEY       = os.environ.get("CLAUDE_API_KEY", "your_claude_api_key")
THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "your_threads_token")
THREADS_USER_ID      = os.environ.get("THREADS_USER_ID", "your_threads_user_id")

# ─────────────────────────────────────────
# 카테고리 + 쿠팡 파트너스 링크
# ─────────────────────────────────────────
CATEGORIES = [
    {
        "name": "자취생 필수템",
        "link": "https://link.coupang.com/a/e0J5NRuVIy",
        "prompt_style": "20~30대 자취생이 무조건 삶의 질 오른다는 꿀템"
    },
    {
        "name": "여름 시즌 아이템",
        "link": "https://link.coupang.com/a/e0J8XB3t7s",
        "prompt_style": "이번 여름 없으면 후회하는 필수 아이템"
    },
    {
        "name": "주방가전",
        "link": "https://link.coupang.com/a/e0KcjeIb7I",
        "prompt_style": "요리 시간 반으로 줄여주는 주방 아이템"
    },
    {
        "name": "영양제/건강식품",
        "link": "https://link.coupang.com/a/e0Ke9Db6uy",
        "prompt_style": "직장인이 매일 챙겨 먹는 영양제"
    },
]


# ─────────────────────────────────────────
# Claude API: 스레드 게시글 생성
# ─────────────────────────────────────────
def generate_post(category: dict) -> str:
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    prompt = f"""
당신은 스레드(Threads)에서 인기 있는 콘텐츠 크리에이터입니다.

아래 조건에 맞는 스레드 게시글을 1개 작성해주세요:

주제: {category['prompt_style']}
말투: 친근하고 가볍게, 약간 유머러스하게
길이: 150~250자 (너무 길면 안 됨)
형식:
- 첫 줄: 관심을 끄는 후킹 문장 (질문형 또는 공감형)
- 중간: 아이템 3~5가지 소개 (이모지 포함)
- 마지막 줄: "링크는 댓글에 있어요 👇" 로 마무리

절대 하지 말 것:
- 광고티 나는 말투 금지
- "~습니다" 같은 딱딱한 어투 금지
- 특정 브랜드명 직접 언급 금지

게시글만 출력하고 설명은 하지 마세요.
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text.strip()


# ─────────────────────────────────────────
# Threads API: 본문 게시 후 post_id 반환
# ─────────────────────────────────────────
def post_to_threads(text: str) -> str | None:
    """게시 성공 시 post_id 반환, 실패 시 None"""

    create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
    payload = {
        "media_type": "TEXT",
        "text": text,
        "access_token": THREADS_ACCESS_TOKEN
    }

    try:
        res = requests.post(create_url, data=payload, timeout=15)
        container_id = res.json().get("id")
        if not container_id:
            print(f"컨테이너 생성 실패: {res.json()}")
            return None

        time.sleep(3)  # Meta 권장 대기

        publish_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        pub_res = requests.post(publish_url, data={
            "creation_id": container_id,
            "access_token": THREADS_ACCESS_TOKEN
        }, timeout=15)

        post_id = pub_res.json().get("id")
        if post_id:
            print(f"✅ 본문 게시 완료 | Post ID: {post_id}")
            return post_id
        else:
            print(f"게시 실패: {pub_res.json()}")
            return None

    except Exception as e:
        print(f"Threads 게시 오류: {e}")
        return None


# ─────────────────────────────────────────
# Threads API: 댓글로 쿠팡 링크 삽입
# ─────────────────────────────────────────
def post_comment(post_id: str, link: str) -> bool:
    """공정위 고지 포함 댓글 게시"""

    comment_text = (
        f"👇 상품 링크\n"
        f"{link}\n\n"
        f"※ 이 포스팅은 쿠팡 파트너스 활동의 일환으로,\n"
        f"이에 따른 일정액의 수수료를 제공받습니다."
    )

    create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
    payload = {
        "media_type": "TEXT",
        "text": comment_text,
        "reply_to_id": post_id,
        "access_token": THREADS_ACCESS_TOKEN
    }

    try:
        res = requests.post(create_url, data=payload, timeout=15)
        container_id = res.json().get("id")
        if not container_id:
            return False

        time.sleep(2)

        pub_res = requests.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
            data={"creation_id": container_id, "access_token": THREADS_ACCESS_TOKEN},
            timeout=15
        )

        if "id" in pub_res.json():
            print("✅ 댓글(링크) 게시 완료")
            return True
        return False

    except Exception as e:
        print(f"댓글 게시 오류: {e}")
        return False


# ─────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"🤖 자동 포스팅 시작: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    # 날짜 기반 카테고리 순환 (매일 다른 카테고리)
    category = CATEGORIES[datetime.now().day % len(CATEGORIES)]
    print(f"📂 카테고리: {category['name']}")
    print(f"🔗 링크: {category['link']}\n")

    # 1. 게시글 생성
    print("✍️  글 생성 중...")
    post_text = generate_post(category)
    print(f"\n{'─'*30}\n{post_text}\n{'─'*30}\n")

    # 2. 본문 게시
    print("📤 스레드에 게시 중...")
    post_id = post_to_threads(post_text)

    if not post_id:
        print("❌ 게시 실패. 종료.")
        return

    # 3. 댓글에 링크 삽입
    time.sleep(2)
    print("💬 링크 댓글 추가 중...")
    post_comment(post_id, category["link"])

    print(f"\n🎉 완료! [{category['name']}]")


if __name__ == "__main__":
    main()
