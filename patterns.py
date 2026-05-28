"""한국어 발화 패턴 감지 (patterns.py)

마커 점수 계산에 쓰이는 어휘 패턴을 중앙 관리합니다.

  has_cta()                  — 구독/좋아요/알림/스폰서 등 홍보성 키워드 포함 여부
  starts_with_continuation() — "근데/사실/그리고/아/음" 등 발화 지속 표현 시작 여부

여기서 패턴을 수정하면 local_breaks.py 점수 계산에 즉시 반영됩니다.
"""

# Continuation openers — the speaker keeps talking past a "completed" sentence.
# When the next sentence starts with one of these, the previous sentence-final
# ending was probably not a true topic break. This is the chat handover doc's
# #1 false-positive cause.
CONTINUATION_OPENERS = (
    "근데", "근까", "그런데", "사실", "아", "어", "음", "그리고", "그래서",
    "그러니까", "그러면", "그럼", "근데요", "참", "아니", "아니야", "아니라",
    "어쨌든", "아무튼", "결국", "막", "뭐", "근데 이제", "그게",
)

# CTA / promotional phrases — markers right next to these are almost always
# bad: the speaker is doing a sponsor read or asking for engagement, not
# closing a topic.
CTA_KEYWORDS = (
    "구독", "좋아요", "알림", "알림설정", "벨", "스폰서", "협찬",
    "후원", "댓글", "공유", "구독자", "구독해", "구독을", "구독은",
    "오늘의 영상", "오늘 영상",
    # "광고" / "채널" are too common in non-CTA contexts to gate on.
)


def starts_with_continuation(text: str) -> bool:
    """True when `text` opens with a continuation marker."""
    t = (text or "").strip().lstrip(".…").strip()
    return any(t.startswith(o) for o in CONTINUATION_OPENERS)


def has_cta(text: str) -> bool:
    """True when `text` contains a CTA keyword."""
    if not text:
        return False
    return any(k in text for k in CTA_KEYWORDS)
