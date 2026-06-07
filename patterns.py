"""한국어 발화 패턴 감지 (patterns.py)

마커 점수 계산에 쓰이는 어휘 패턴을 중앙 관리합니다.

  starts_with_continuation() — "근데/사실/그리고/아/음" 등 발화 지속 표현 시작 여부

여기서 패턴을 수정하면 local_breaks.py 점수 계산에 즉시 반영됩니다.
"""

# Continuation openers — the speaker keeps talking past a "completed" sentence.
# When the next sentence starts with one of these, the previous sentence-final
# ending was probably not a true topic break.
CONTINUATION_OPENERS = (
    "근데", "근까", "그런데", "사실", "아", "어", "음", "그리고", "그래서",
    "그러니까", "그러면", "그럼", "근데요", "참", "아니", "아니야", "아니라",
    "어쨌든", "아무튼", "결국", "막", "뭐", "근데 이제", "그게",
)


def starts_with_continuation(text: str) -> bool:
    """True when `text` opens with a continuation marker."""
    t = (text or "").strip().lstrip(".…").strip()
    return any(t.startswith(o) for o in CONTINUATION_OPENERS)
