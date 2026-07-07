"""
스레드 + 쿠팡 파트너스 자동화 포스터
──────────────────────────────────────────────────────────────
네이버 쇼핑 API로 실제 인기 상품 데이터를 수집한 뒤
Claude Sonnet이 꿀템연구소 감성으로 텍스트 게시글 생성
PC가 꺼져 있어도 GitHub Actions로 실행 가능

필요 패키지: pip install anthropic requests
"""

import os
import time
import hashlib
import requests
from datetime import datetime
import anthropic

import naver_shopping  # 공용 네이버 쇼핑 모듈

# ─────────────────────────────────────────
# 환경 변수 (GitHub Secrets에 등록)
# ─────────────────────────────────────────
CLAUDE_API_KEY       = os.environ.get("CLAUDE_API_KEY", "")
THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
THREADS_USER_ID      = os.environ.get("THREADS_USER_ID", "")
# NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 은 naver_shopping 모듈에서 직접 읽음

# ─────────────────────────────────────────
# 카테고리 + 쿠팡 파트너스 링크
# ─────────────────────────────────────────
CATEGORIES = [
    {
        "name":        "자취생 필수템",
        "link":        "https://link.coupang.com/a/e0J5NRuVIy",
        "naver_query": "자취 필수템 생활용품",
        "prompts": [
            "꿀템연구소 연구원이 자취방 생활 3년간 직접 써보고 '이거 없이 어떻게 살았지?' 싶었던 아이템만 엄선.",
            "자취 1년차 때 몰랐던 것들. 꿀템연구소가 직접 테스트해서 검증한 자취방 업그레이드 아이템.",
            "월세 내고 남는 돈으로 이것만 사도 자취방 퀄리티 완전 달라짐. 꿀템연구소 자취방 연구 결과 공유.",
        ],
    },
    {
        "name":        "여름 시즌 아이템",
        "link":        "https://link.coupang.com/a/e0J8XB3t7s",
        "naver_query": "여름 더위 냉감 용품 추천",
        "prompts": [
            "꿀템연구소가 올여름 더위템 10개 직접 써봤는데 진짜 효과 있는 건 이것뿐이었음.",
            "에어컨 전기세 폭탄 맞기 전에 알았으면 좋았을 여름 꿀조합. 연구소가 실제로 전기세 비교해봄.",
            "해외에서 매년 여름마다 난리나는 더위 템, 한국판으로 찾아봤더니 이게 있었음.",
        ],
    },
    {
        "name":        "주방가전",
        "link":        "https://link.coupang.com/a/e0KcjeIb7I",
        "naver_query": "자취 소형 주방가전 추천",
        "prompts": [
            "요리 못해도 이 가전 하나면 밥집 수준 나옴. 꿀템연구소가 3개월 직접 써보고 검증한 결과 보고서.",
            "편의점 도시락 끊게 만든 주방 아이템들. 꿀템연구소 귀차니스트 연구원이 요리 시간 재봤더니 10분 단축됨.",
            "주방 이것만 있으면 요리 IQ 30 상승. 꿀템연구소가 직접 테스트함.",
        ],
    },
    {
        "name":        "영양제/건강식품",
        "link":        "https://link.coupang.com/a/e0Ke9Db6uy",
        "naver_query": "20대 직장인 영양제 추천",
        "prompts": [
            "영양제 6개월 먹어봤는데 효과 있는 건 이것뿐이었음. 꿀템연구소 건강 연구원의 솔직한 돈값 평가.",
            "20대 직장인 영양제 뭐 먹어야 할지 몰라서 꿀템연구소가 대신 알아봄.",
            "피부, 피로, 수면 다 잡는다는 영양제들 꿀템연구소가 직접 먹어봤는데 진짜 되는 건 이거였음.",
        ],
    },
]


# ─────────────────────────────────────────
# 프롬프트 순환 (시간 기반 해시)
# ─────────────────────────────────────────
def pick_prompt(category: dict) -> str:
    seed = datetime.now().strftime("%Y%m%d%H")
    idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(category["prompts"])
    return category["prompts"][idx]


# ─────────────────────────────────────────
# Claude API: 게시글 생성
# ─────────────────────────────────────────
def generate_post(category: dict, products: list[dict]) -> str:
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    selected_prompt = pick_prompt(category)
    product_block = naver_shopping.format_for_prompt(products)

    product_section = ""
    if product_block:
        product_section = f"""
아래는 오늘 네이버 쇼핑 실시간 인기 상품 데이터야.
이 중 2~3개를 자연스럽게 글에 녹여줘 (상품명 그대로 언급해도 됨, 가격은 참고용):
{product_block}
"""

    prompt = f"""
너는 '꿀템연구소' 연구원이야.
진짜 컨셉: 소비생활을 과학적으로 연구하는 덕후. 광고주 없음. 돈 받고 쓰는 글 아님.
연구소 특성상 실패한 템도 솔직하게 공유함. 그래서 팔로워들이 믿음.

{product_section}
오늘 연구 주제: "{selected_prompt}"

[꿀템연구소 문체 규칙 — 이걸 지켜야 진짜처럼 보임]

▸ 첫 줄 형식 (딱 하나만 골라):
  - "XX 사면서 깨달은 것" / "XX 쓰다가 발견한 사실"
  - "가설: XX / 결과: XX" (실험 리포트 형식)
  - 반박 불가 팩트로 시작 ("자취방 지저분한 건 의지 문제 아님")
  - 공감각 후킹 ("새벽 2시에 이거 주문한 사람 나야")

▸ 본문 스타일:
  - 연구 결과처럼 써 (가설→실험→결론 구조)
  - 실패 경험 1개 섞기 ("처음엔 XX 샀다가 망했음")
  - 가격 말할 때: "X만원대인데 진짜임" / "X천원짜리가 이러면 안 되는데 됨"
  - "ㄹㅇ", "팩트", "반박시 님말맞음", "근데 진짜로" 자연스럽게 섞기
  - 연구원 캐릭터 살리기: "연구 결과", "실험 완료", "데이터 있음"

▸ 절대 쓰면 안 되는 표현:
  ❌ 추천합니다 / 강추 / 놓치지 마세요 / 지금 바로 / 클릭
  ❌ 이 제품은 ~한 특징이 있습니다
  ❌ 가격 대비 훌륭한 / 퀄리티가 좋은
  ❌ ~하실 분들께 / ~를 원하신다면
  → 이런 표현 나오면 광고임. 절대 금지.

▸ 마무리: 자연스러운 한 줄 + "링크는 댓글에 👇"
▸ 글자 수: 180~320자
▸ 줄바꿈 적절히 (3~4줄마다)

게시글만 출력해. 설명 붙이지 마.
"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()


# ─────────────────────────────────────────
# Threads API: 본문 게시
# ─────────────────────────────────────────
def post_to_threads(text: str) -> str | None:
    create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
    try:
        res = requests.post(create_url, data={
            "media_type": "TEXT",
            "text": text,
            "access_token": THREADS_ACCESS_TOKEN,
        }, timeout=15)
        container_id = res.json().get("id")
        if not container_id:
            print(f"컨테이너 생성 실패: {res.json()}")
            return None

        time.sleep(3)

        pub = requests.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
            data={"creation_id": container_id, "access_token": THREADS_ACCESS_TOKEN},
            timeout=15,
        )
        post_id = pub.json().get("id")
        if post_id:
            print(f"✅ 본문 게시 완료 | {post_id}")
            return post_id
        print(f"게시 실패: {pub.json()}")
        return None

    except Exception as e:
        print(f"Threads 게시 오류: {e}")
        return None


# ─────────────────────────────────────────
# Threads API: 댓글에 쿠팡 링크 삽입
# ─────────────────────────────────────────
def post_comment(post_id: str, link: str) -> bool:
    comment_text = (
        "👇 상품 링크\n"
        f"{link}\n\n"
        "※ 이 포스팅은 쿠팡 파트너스 활동의 일환으로,\n"
        "이에 따른 일정액의 수수료를 제공받습니다."
    )
    try:
        res = requests.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
            data={
                "media_type": "TEXT",
                "text": comment_text,
                "reply_to_id": post_id,
                "access_token": THREADS_ACCESS_TOKEN,
            }, timeout=15,
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
        if "id" in pub.json():
            print("✅ 댓글(링크) 게시 완료")
            return True
        return False

    except Exception as e:
        print(f"댓글 게시 오류: {e}")
        return False


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────
def main():
    print(f"\n{'='*52}")
    print(f"🤖 꿀템연구소 텍스트 포스팅 시작: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*52}\n")

    # 날짜+시간 기반 카테고리 순환 (8시간마다 변경 → 하루 3번 포스팅이 모두 다른 카테고리)
    now = datetime.now()
    cat = CATEGORIES[(now.day + now.hour // 8) % len(CATEGORIES)]
    print(f"📂 카테고리: {cat['name']} (날짜:{now.day} 시간대:{now.hour//8})")

    # 1. 네이버 쇼핑 실제 상품 수집
    print("\n🛍️  네이버 쇼핑 상품 수집 중...")
    products = naver_shopping.fetch_products(cat["naver_query"], count=5)

    # 2. Claude로 게시글 생성
    print("\n✍️  글 생성 중...")
    post_text = generate_post(cat, products)
    print(f"\n{'─'*40}\n{post_text}\n{'─'*40}\n")

    # 3. 본문 게시
    print("📤 스레드에 게시 중...")
    post_id = post_to_threads(post_text)
    if not post_id:
        print("❌ 게시 실패. 종료.")
        return

    # 4. 댓글에 링크 삽입
    time.sleep(2)
    print("💬 링크 댓글 추가 중...")
    post_comment(post_id, cat["link"])

    print(f"\n🎉 완료! [{cat['name']}]")


if __name__ == "__main__":
    main()
