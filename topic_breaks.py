"""Whisper 세그먼트 → 문장 단위 병합 (topic_breaks.py)

Whisper는 짧은 세그먼트 단위로 전사하는데, 마커 생성에는 완성된 문장 단위가 필요합니다.
한국어 종결어미(~다/~요/~까/~죠 등)를 기준으로 세그먼트를 합쳐 문장을 만듭니다.

  build_sentences()       — 세그먼트 리스트 → 문장 리스트 [{start, end, text}]
  build_transcript_text() — 문장 리스트 → [HH:MM:SS] 형식 텍스트 (디버그용)

문법 기반 병합이라 Whisper 원본의 타임스탬프 갭과 무관하게 동작합니다.
Path 2 컷 앵커 경로에서 in_gap 체크 시 원본 세그먼트도 병행 확인하는 이유입니다.
"""
import json

DEFAULT_MODEL = "gpt-4o-mini"
SENT_END = ".?!"


def _hms(seconds):
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _hms_to_seconds(s):
    parts = [int(p) for p in str(s).strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, sec = parts[-3], parts[-2], parts[-1]
    return h * 3600 + m * 60 + sec


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


def build_transcript_text(sentences):
    """Render sentences as '[HH:MM:SS] sentence' lines (timecode = sentence end)."""
    return "\n".join(f"[{_hms(s['end'])}] {s['text']}" for s in sentences)


SYSTEM_PROMPT = (
    "당신은 유튜브 영상의 중간광고(ad break) 위치를 정하는 편집 보조 도구다.\n"
    "절대 규칙:\n"
    "- 광고는 반드시 '한 문장이 완전히 끝난 직후'에 들어간다. 문장 중간·설명 도중은 절대 안 된다.\n"
    "- 입력 자막은 이미 문장 단위로 나뉘어 있고, 각 줄은 [HH:MM:SS] (그 문장이 끝나는 시각)로 시작한다.\n"
    "- 너는 자막에 실제로 존재하는 줄의 [HH:MM:SS] 값만 답으로 쓸 수 있다. "
    "임의의 시각(예: 00:10:00)을 만들어내지 마라.\n"
    "- 여러 문장이 한 이야기를 이루다 마무리되는 지점 — 다음 문장이 새 이야기를 시작하기 "
    "직전의 문장 끝 — 을 우선한다."
)


def build_user_prompt(transcript_text, duration):
    return (
        f"영상 길이: {_hms(duration)}\n\n"
        "다음 제약 안에서 중간광고 지점을 골라라:\n"
        "- 첫 광고: 00:03:00 ~ 00:10:00 사이\n"
        "- 광고와 광고 사이 간격: 10분 ~ 15분\n"
        "- 영상 시작 3분 이내, 끝 3분 이내 금지\n"
        "- 광고 개수는 위 간격 규칙에 맞게 스스로 결정 "
        "(짧은 영상 1~2개, 긴 영상 3개 이상 가능)\n\n"
        "이 제약은 '후보 구간'만 정한다. 제약을 맞추려고 문장 중간이나 어색한 곳을 "
        "고르지 마라 — 구간 안에서 문장이 가장 자연스럽게 끝나는 줄을 골라라.\n\n"
        "아래 JSON 형식으로만 답하라. timecode 는 자막에 실제로 있는 줄의 값을 그대로 복사:\n"
        '{"ad_breaks": [{"timecode": "HH:MM:SS", "ended_sentence": "그 줄의 문장", '
        '"next_sentence": "바로 다음 줄의 문장", "reason": "여기가 좋은 마침점인 이유"}]}\n\n'
        "=== 자막 (문장 단위) 시작 ===\n"
        f"{transcript_text}\n"
        "=== 자막 끝 ==="
    )


def select_ad_breaks(segments, duration, client, model=DEFAULT_MODEL):
    """Return list of {time, timecode, ended_sentence, next_sentence, reason}.

    Each returned time is snapped to the nearest real sentence boundary, so the
    ad break always lands exactly at a completed sentence.
    """
    sentences = build_sentences(segments)
    sentence_ends = [s["end"] for s in sentences]
    transcript_text = build_transcript_text(sentences)

    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(transcript_text, duration)},
        ],
    )
    data = json.loads(resp.choices[0].message.content)

    breaks = []
    for b in data.get("ad_breaks", []):
        raw = _hms_to_seconds(b["timecode"])
        # snap to the nearest actual sentence boundary
        snapped = min(sentence_ends, key=lambda e: abs(e - raw)) if sentence_ends else raw
        breaks.append({
            "time": snapped,
            "timecode": _hms(snapped),
            "ended_sentence": b.get("ended_sentence", ""),
            "next_sentence": b.get("next_sentence", ""),
            "reason": b.get("reason", ""),
        })
    breaks.sort(key=lambda x: x["time"])
    return breaks


def make_client():
    """Build an OpenAI client, loading OPENAI_API_KEY from .env if present."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from openai import OpenAI
    return OpenAI()
