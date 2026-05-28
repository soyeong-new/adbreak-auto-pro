"""한국어 텍스트 의미 유사도 — 주제 전환 감지 (text_similarity.py)

컷 전후 발화를 로컬 한국어 임베딩 모델(jhgan/ko-sroberta-multitask)로 벡터화해
코사인 유사도를 계산합니다. 결과는 Path 2 마커의 주제 전환 점수에 사용됩니다.

  유사도 < 0.75 → 주제 전환 → +4.0점 (W_TOPIC_CHANGE)
  유사도 ≥ 0.75 → 주제 유지 → 점수 변화 없음

주요 함수:
  compute_text_similarity()  — 단건 계산 (테스트용)
  batch_text_similarities()  — 전체 컷 배치 계산 + 캐싱 ({영상명}.text_sim.json)

컷 전후 각 30초(TEXT_WINDOW) 구간의 발화를 사용합니다.
"""

import json
import os

_model = None
TEXT_WINDOW = 30.0          # seconds of transcript to gather on each side
MODEL_NAME = "jhgan/ko-sroberta-multitask"


def _load():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _gather_text(segments, cut_t, window, side):
    """Collect transcript text on one side of the cut.

    side = 'before' : segments whose end <= cut_t, within window seconds
    side = 'after'  : segments whose start >= cut_t, within window seconds
    Returns a single concatenated string, or '' if nothing found.
    """
    parts = []
    if side == "before":
        for seg in reversed(segments):
            if seg["end"] > cut_t:
                continue
            if cut_t - seg["end"] > window:
                break
            parts.insert(0, seg["text"].strip())
    else:
        for seg in segments:
            if seg["start"] < cut_t:
                continue
            if seg["start"] - cut_t > window:
                break
            parts.append(seg["text"].strip())
    return " ".join(parts)


def compute_text_similarity(segments, cut_t, window=TEXT_WINDOW):
    """Cosine similarity between transcript before and after cut_t.

    Returns float in [-1, 1], or None if either side has no text.
    High value → same topic. Low value → topic changed.
    """
    before = _gather_text(segments, cut_t, window, "before")
    after  = _gather_text(segments, cut_t, window, "after")
    if not before or not after:
        return None

    import torch
    model = _load()
    embs = model.encode([before, after], convert_to_tensor=True,
                        normalize_embeddings=True)
    sim = float(torch.dot(embs[0], embs[1]).item())
    return round(sim, 4)


# ---------------------------------------------------------------------------
# Batch mode with per-video cache
# ---------------------------------------------------------------------------

def _text_cache_path(video_path):
    from pipeline import CACHE_DIR
    st = os.stat(video_path)
    key = f"{os.path.splitext(os.path.basename(video_path))[0]}_{st.st_size}"
    return os.path.join(CACHE_DIR, f"{key}.text_sim.json")


def batch_text_similarities(video_path, segments, cut_times,
                             window=TEXT_WINDOW, progress=None):
    """Compute text similarity for every cut in cut_times.

    Returns dict {cut_time (float): similarity (float | None)}.
    Results are cached so re-running is instant.
    """
    if not cut_times:
        return {}

    cache_path = _text_cache_path(video_path)
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cached = {float(k): v for k, v in json.load(f).items()}
    else:
        cached = {}

    missing = [c for c in cut_times if c not in cached]
    if not missing:
        return {c: cached[c] for c in cut_times}

    if progress:
        progress(f"텍스트 주제 유사도 분석 중 ({len(missing)}개)...")

    # Gather (before, after) text pairs for all missing cuts
    pairs = {}
    for c in missing:
        before = _gather_text(segments, c, window, "before")
        after  = _gather_text(segments, c, window, "after")
        if before and after:
            pairs[c] = (before, after)
        else:
            cached[c] = None

    if pairs:
        import torch
        model = _load()
        cuts_ordered = sorted(pairs)
        texts = []
        for c in cuts_ordered:
            texts.append(pairs[c][0])   # before
            texts.append(pairs[c][1])   # after

        embs = model.encode(texts, convert_to_tensor=True,
                            normalize_embeddings=True, show_progress_bar=False)

        for i, c in enumerate(cuts_ordered):
            ea = embs[2 * i]
            eb = embs[2 * i + 1]
            sim = float(torch.dot(ea, eb).item())
            cached[c] = round(sim, 4)

    # Persist cache
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in cached.items()}, f)

    return {c: cached.get(c) for c in cut_times}
