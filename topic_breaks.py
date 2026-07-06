"""Whisper 세그먼트 → 문장 단위 병합 (topic_breaks.py)

Whisper는 짧은 세그먼트 단위로 전사하는데, 마커 생성에는 완성된 문장 단위가 필요합니다.
한국어 종결어미(~다/~요/~까/~죠 등)를 기준으로 세그먼트를 합쳐 문장을 만듭니다.

  build_sentences() — 세그먼트 리스트 → 문장 리스트 [{start, end, text}]

문법 기반 병합이라 Whisper 원본의 타임스탬프 갭과 무관하게 동작합니다.
Path 2 컷 앵커 경로에서 in_gap 체크 시 원본 세그먼트도 병행 확인하는 이유입니다.
"""

SENT_END = ".?!"


# Korean sentence-final endings — speed-independent (morphology, not duration).
_FINAL_ENDINGS = (
    "니다", "습니까", "거든요", "잖아요", "는데요", "죠", "요", "다", "까",
    "라", "자", "네", "군요", "구나", "더라", "야", "어", "지",
)
# Connective / particle tails that look final but are mid-sentence.
_CONNECTIVES = (
    "다고", "다가", "다는", "다며", "다면", "어서", "아서", "는데", "지만",
    "거나", "니까", "으니", "면서", "는", "은", "을", "를", "고", "서",
    "에", "의", "도", "만", "과", "와", "채",
)


def _is_sentence_final(core):
    """True if `core` (text with trailing punctuation stripped) ends a sentence."""
    if not core:
        return False
    if core.endswith("습니까"):
        return True
    if core.endswith("니까"):          # 그러니까 / 하니까 — connective
        return False
    if core.endswith(_CONNECTIVES):
        return False
    return core.endswith(_FINAL_ENDINGS)


def build_sentences(segments):
    """Merge Whisper segments into sentences.

    A boundary is taken at sentence-final punctuation or a Korean sentence-final
    ending. This is independent of speech speed (it keys on grammar, not on a
    duration cap), so fast and slow speakers are handled the same way.

    Returns list of {start, end, text}. Each entry's `end` is an ad break point.
    """
    sentences = []
    buf, start = [], None
    for s in segments:
        txt = s["text"].strip()
        if not txt:
            continue
        if start is None:
            start = s["start"]
        buf.append(txt)
        joined = " ".join(buf).strip()
        core = joined.rstrip(SENT_END + " ")
        if joined and (joined[-1] in SENT_END or _is_sentence_final(core)):
            sentences.append({"start": start, "end": s["end"], "text": joined})
            buf, start = [], None
    if buf:
        sentences.append({"start": start, "end": segments[-1]["end"],
                           "text": " ".join(buf).strip()})
    return sentences
