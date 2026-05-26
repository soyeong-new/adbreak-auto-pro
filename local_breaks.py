"""Local (no-API) ad break detection.

A sentence boundary becomes a marker only when a real silence follows the
completed sentence -- the voice-band loudness must drop most of the way (a
SILENCE_K fraction) from the local speech level toward the video's own noise
floor and stay there for SILENCE_MIN s. The threshold is adaptive: a video
with a narrow dynamic range is judged on the same relative scale as a wide
one. This is what keeps a marker off mid-sentence speech: a sentence-final
ending (~다/~요/~까…) alone is
not enough, because in speech people tack words on after the verb; the speaker
must actually pause. The marker is then placed on an allowed 29.97 NDF frame
(:00 first, else :01-03/:28-29) *inside* that silence.

Markers come in two kinds:

  transition : the silence coincides with a scene cut, and CLIP (analyzer.py)
               confirms a real scene transition. The strong ad break spot.
  reference  : sentence end + real silence but no scene cut. A fallback for
               formats with no visual scene transitions (single-room videos).

Two views of the same markers:
  1st pass : a spaced subset -- first ad 3-10 min, then one every 10-15 min.
  2nd pass : every marker, with no distance/count limit.
"""
import re
import math
from topic_breaks import build_sentences
from framecode import (FPS, FF_TOP, FF_CANDIDATE, FF_ALLOWED, frame_to_seconds,
                       frame_tier, frame_to_timecode)
from patterns import starts_with_continuation, has_cta

DEFAULTS = {
    "intro_deadzone": 180.0,
    "outro_deadzone": 180.0,
    "first_min": 180.0,
    "first_max": 600.0,
    "gap_min": 600.0,
    "gap_max": 900.0,
    "n_alternatives": 5,       # markers shown per 1st-pass ad slot
    # --- v1.1 heuristic toggles. Defaults preserve v1.0 behavior. ---
    # When True, a sentence pair where the next sentence opens with a
    # continuation marker ("근데/사실/그리고/아/음/…") is dropped. A penalty
    # of P_CONTINUATION is always applied (whether or not the marker is kept).
    "exclude_continuation": False,
    # When True, a sentence pair where prev or next contains a CTA keyword
    # ("구독/좋아요/알림/스폰서/…") is dropped. P_CTA always applied.
    "exclude_cta": False,
    # Minimum score required to keep a marker. None = no cutoff (v1.0 default).
    "min_score": None,
}

STRONG_OPENERS = [
    "자 이제", "자, 이제", "자 그러면", "자 그럼", "자, 그러면", "자, 그럼",
    "그러면 이제", "그럼 이제", "다음으로", "다음은", "그 다음", "마지막으로",
    "끝으로", "오늘은", "이번에는", "이번엔", "이번 시간", "첫 번째", "두 번째",
    "세 번째", "정리하자면", "정리하면", "요약하면", "본론",
]
WEAK_OPENERS = ["자 ", "자,", "자아", "이제 ", "이제는", "그러면 ", "그럼 ", "여러분"]
CLOSERS = [
    "겠습니다", "드리겠습니다", "말씀드릴게요", "드릴게요", "이상입니다",
    "마치겠습니다", "끝",
]

W_STRONG_OPENER = 3.0
W_WEAK_OPENER = 1.0
W_CLOSER = 2.0
W_SCENE = 5.0
P_CLIP_FAIL = 5.0  # CLIP 재검증 실패 시 패널티 (장면전환 아님으로 판정)
SCENE_RADIUS = 0.3
# 2026-05-24: CLIP-확인된 컷에 대해 SCENE_RADIUS를 넓혀 silence 기반 후보를 has_cut=True로
# 업그레이드. 새 후보를 생성하는 게 아니라 기존 후보의 has_cut 상태만 교정.
SCENE_RADIUS_CLIP = 1.0
W_PAUSE = 0.0    # 2026-05-23: 긴 침묵 보너스 제거. 데이터 분석 결과 long_silence 마커의
                 # 96%(232/238)가 false positive. GT hit 비율(12.5%)도 miss(18.2%)보다 낮았음.
W_FRAME_TOP = 1.0  # 2026-05-24: 2.0→1.0. :00 프레임 단독으로 discourse 없는 컷이
                   # W_SCENE+W_FRAME_TOP=4.5 받아 GT reference 마커를 이기던 문제 수정.
P_SHORT = 1.5
P_QA = 1.0
# v1.1 penalties — applied when the corresponding pattern is detected,
# regardless of whether `exclude_*` is on. With excludes off, a CTA/
# continuation marker survives but is demoted below borderline neutral
# markers. This keeps recall as-is while marking suspicious cases low.
P_CONTINUATION = 2.0
P_CTA = 3.0

SILENCE_K = 0.78        # silence depth as a fraction of the speech-to-floor range
SILENCE_MIN = 0.5       # seconds of continuous silence a real pause needs
SILENCE_SEARCH = 0.3    # slack (s) around the sentence gap to scan for silence
LONG_SILENCE = 0.8      # silence at least this long scores a bonus

# Cut-anchor path (Path 2): 문장 경계 탐색 창.
# 침묵 불필요. 컷 ±CUT_BOUNDARY_WINDOW 초 안에 Whisper 문장 경계가
# 존재하면 "말이 연속적이지 않은 구간"으로 판단.
CUT_BOUNDARY_WINDOW = 0.5  # 문장 간 간격 판단 여유(s) — Whisper 타임스탬프 오차 허용

# 텍스트 의미 유사도 기준. 이 값 미만이면 주제가 전환된 것으로 판단.
# 1.0 = 완전히 같은 내용, 0.0 = 전혀 다른 내용.
# 0.75: 같은 맥락이지만 주제가 조금 다름 / 미만이면 뚜렷한 주제 전환.
TEXT_SIM_THRESHOLD = 0.75
W_TOPIC_CHANGE = 4.0   # 주제 전환 확인 시 추가 점수 (2.0→4.0)


def _strip_punct(text):
    return text.strip().rstrip(".?!… ").strip()


def _local_speech_db(voice_env, t):
    """Median voice-band loudness over ~16 s around t -- the local speech level."""
    db, rate = voice_env["db"], voice_env["rate"]
    w = int(8 * rate)
    i = int(round(t * rate))
    seg = db[max(0, i - w):min(len(db), i + w)]
    if not seg:
        return None
    return sorted(seg)[len(seg) // 2]


def _noise_floor(voice_env):
    """The video's own quiet baseline -- the 5th-percentile voice-band loudness.
    The bottom of the adaptive silence scale."""
    db = sorted(voice_env["db"])
    return db[len(db) // 20] if db else -70.0


def _find_silence(voice_env, t0, t1, noise_floor, min_dur=None):
    """Find a real pause in [t0, t1].

    A sample counts as silent when its loudness has dropped a SILENCE_K
    fraction of the way from the local speech level down to the video's noise
    floor -- an adaptive threshold, so a narrow- and a wide-dynamic-range video
    are judged on the same relative scale. Returns (start, end) of the first
    continuous stretch of at least min_dur s (default: SILENCE_MIN), or None
    if the speech runs straight through (not a real sentence break).

    min_dur : override SILENCE_MIN for the cut-anchor path (Path 2) which uses
              a looser threshold (CUT_SILENCE_MIN=0.2s) to capture any sign of
              speech non-continuity near a CLIP-confirmed scene cut.
    """
    if not voice_env or not voice_env.get("db"):
        return None
    db, rate = voice_env["db"], voice_env["rate"]
    speech = _local_speech_db(voice_env, (t0 + t1) / 2.0)
    if speech is None:
        return None
    thresh = (1.0 - SILENCE_K) * speech + SILENCE_K * noise_floor
    if min_dur is None:
        min_dur = SILENCE_MIN
    need = max(1, int(min_dur * rate))
    i0 = max(0, int(t0 * rate))
    i1 = min(len(db), int(t1 * rate))
    run = 0
    for i in range(i0, i1 + 1):
        if i < i1 and db[i] < thresh:
            run += 1
        else:
            if run >= need:
                return ((i - run) / rate, i / rate)
            run = 0
    return None


def _allowed_frame_in(s0, s1):
    """Pick an allowed 29.97 NDF frame inside the silence [s0, s1]: the earliest
    :00 frame if the silence holds one, else the earliest :01-03/:28-29 frame.
    None if the silence is too short to contain any allowed frame."""
    f0 = math.ceil(s0 * FPS)
    f1 = math.floor(s1 * FPS)
    cand = None
    for f in range(f0, f1 + 1):
        ff = f % 30
        if ff in FF_TOP:
            return f
        if cand is None and ff in FF_CANDIDATE:
            cand = f
    return cand


def _nearest_allowed_frame(t):
    """장면 컷 시간 t에서 가장 가까운 허용 프레임(:00/:01/:02/:03/:28/:29)을 반환.

    컷 위치에서 바깥쪽으로 탐색해 FF_ALLOWED에 해당하는 첫 프레임을 돌려줌.
    최대 ±1초(~30프레임) 범위를 탐색; 없으면 None.
    """
    f_center = int(round(t * FPS))
    for delta in range(0, 31):
        for f in (f_center + delta, f_center - delta):
            if f >= 0 and f % 30 in FF_ALLOWED:
                return f
    return None


def _score(ended, nxt, frame, has_cut, cut_dist, silence_len):
    """Score a marker. Returns (score, reasons, has_signal, kill_reason).

    Every marker already sits in a verified silence after a completed sentence;
    this only ranks them. has_signal: a discourse cue (a topic-shift opener on
    the next sentence, or a closing phrase on the ended one) is present.
    kill_reason: a short tag (str) when this pair matches a v1.1 exclude
    pattern, else None. The caller decides whether to drop the marker based on
    its settings — `_score` itself never drops anything.
    """
    score = 0.0
    reasons = [f"문장 끝 직후 {silence_len:.1f}초 침묵"]
    signal = False
    kill_reason = None

    if has_cut:
        score += W_SCENE
        reasons.append(f"장면 컷에서 시작({cut_dist:.2f}s)")

    nxt_s = nxt.strip()
    # The continuation check runs *before* STRONG/WEAK openers. STRONG_OPENERS
    # starts with phrases like "자 그러면" / "자 이제" that begin with single
    # tokens also in CONTINUATION_OPENERS ("자/그러면/이제" etc.) only
    # superficially — STRONG patterns are multi-word and more specific, so
    # checking continuation first does not steal credit from them.
    if starts_with_continuation(nxt_s):
        score -= P_CONTINUATION
        reasons.append("다음 문장이 발화 지속 표현으로 시작(연결 가능성)")
        kill_reason = "continuation"
    elif any(nxt_s.startswith(o) for o in STRONG_OPENERS):
        score += W_STRONG_OPENER
        reasons.append("다음 문장이 화제 전환 표현으로 시작")
        signal = True
    elif any(nxt_s.startswith(o) for o in WEAK_OPENERS):
        score += W_WEAK_OPENER
        reasons.append("다음 문장이 전환 표현으로 시작")
        signal = True

    ended_core = _strip_punct(ended)
    if any(ended_core.endswith(c) for c in CLOSERS):
        score += W_CLOSER
        reasons.append("앞 문장이 마무리 표현으로 종료")
        signal = True

    if silence_len >= LONG_SILENCE:
        score += W_PAUSE
        reasons.append(f"긴 침묵({silence_len:.1f}초)")

    if frame % 30 == 0:
        score += W_FRAME_TOP
        reasons.append("최우선 :00 프레임")

    if len(ended_core) < 8:
        score -= P_SHORT
        reasons.append("앞 문장이 너무 짧음(조각)")
    if ended.strip().endswith("?") or re.search(r"(냐|까|까요|나요)$", ended_core):
        score -= P_QA
        reasons.append("앞 문장이 질문(자문자답 중간 가능성)")

    if has_cta(ended) or has_cta(nxt):
        score -= P_CTA
        reasons.append("CTA/홍보 키워드 인접")
        # CTA wins over continuation as the kill reason — it's a stronger
        # signal that the marker should not exist.
        kill_reason = "cta"

    return score, reasons, signal, kill_reason


def select_ad_breaks_local(segments, duration, settings=None,
                           scene_cuts=None, voice_env=None,
                           clip_real_cuts=None, text_sims=None):
    """Return a flat, time-sorted list of every candidate marker.

    Each: {time, frame, timecode, tier, has_cut, has_signal, score, reason,
    ended_sentence, next_sentence}. has_cut True = transition candidate (needs
    CLIP), False = reference marker.

    clip_real_cuts : set of cut times (float seconds) that batch CLIP already
        confirmed as genuine scene transitions (similarity < SAME_THRESHOLD).
        When provided, every such cut in the valid range that is not already
        covered by a silence-based candidate gets its own cut-anchor candidate.
        This is the primary signal for highly-edited shows (e.g. YBJ) where
        ad-break points often lack the 0.5 s silence the original path requires.
    """
    s = {**DEFAULTS, **(settings or {})}
    sentences = build_sentences(segments)
    if len(sentences) < 2:
        return []
    cuts = sorted(scene_cuts) if scene_cuts else []
    real_cuts = set(clip_real_cuts) if clip_real_cuts else set()

    lo = s["intro_deadzone"]
    hi = duration - s["outro_deadzone"]

    # the video's own noise floor -- bottom of the adaptive silence scale
    noise_floor = (_noise_floor(voice_env)
                   if voice_env and voice_env.get("db") else -70.0)

    markers = []

    # ------------------------------------------------------------------
    # Path 1 (original): sentence boundary + verified silence
    # ------------------------------------------------------------------
    for i in range(len(sentences) - 1):
        ended, nxt = sentences[i], sentences[i + 1]

        # A real silence must follow the completed sentence -- otherwise the
        # speech runs straight through and this is not a true sentence break.
        sil = _find_silence(voice_env, ended["end"] - SILENCE_SEARCH,
                            nxt["start"] + SILENCE_SEARCH, noise_floor)
        if sil is None:
            continue
        # Place the marker on an allowed frame *inside* that silence.
        frame = _allowed_frame_in(*sil)
        if frame is None:                      # silence holds no allowed frame
            continue
        marker_time = frame_to_seconds(frame)
        if not (lo <= marker_time <= hi):
            continue

        # A scene cut coinciding with the silence -> transition candidate.
        # Primary check: any cut within SCENE_RADIUS.
        # Extended check: CLIP-confirmed cut within SCENE_RADIUS_CLIP (wider).
        # The extended check only upgrades existing silence-based markers —
        # it never generates new candidates.
        has_cut, cut_dist = False, 0.0
        clip_preconfirmed = False
        if cuts:
            cut = min(cuts, key=lambda c: abs(c - marker_time))
            dist = abs(cut - marker_time)
            if dist <= SCENE_RADIUS:
                has_cut, cut_dist = True, dist
            elif real_cuts and dist <= SCENE_RADIUS_CLIP and cut in real_cuts:
                has_cut, cut_dist = True, dist
                clip_preconfirmed = True

        tier = frame_tier(frame)
        sc, reasons, signal, kill_reason = _score(
            ended["text"], nxt["text"], frame, has_cut, cut_dist,
            sil[1] - sil[0])

        # v1.1 exclusion: optional, off by default. exclude_* tells the
        # detector to drop markers whose kill_reason matches.
        if kill_reason == "continuation" and s.get("exclude_continuation"):
            continue
        if kill_reason == "cta" and s.get("exclude_cta"):
            continue
        # v1.1 minimum score cutoff: optional, off by default.
        if s.get("min_score") is not None and sc < s["min_score"]:
            continue

        m = {
            "time": marker_time,
            "frame": frame,
            "timecode": frame_to_timecode(frame),
            "tier": tier,
            "has_cut": has_cut,
            "has_signal": signal,
            "score": round(sc, 2),
            "reason": "; ".join(reasons),
            "ended_sentence": ended["text"],
            "next_sentence": nxt["text"],
            "kill_reason": kill_reason,
        }
        if clip_preconfirmed:
            m["clip_preconfirmed"] = True
        markers.append(m)

    # ------------------------------------------------------------------
    # Path 2 (cut-anchor): CLIP-confirmed real transitions that are not
    # already covered by a silence-based candidate.
    #
    # For highly-edited shows the editor places cuts at ad-break boundaries
    # regardless of whether there is a 0.5 s audio silence; those cuts are
    # the primary signal.  We generate one candidate per such cut and let the
    # existing _verify / pick_primary logic handle the rest.
    # ------------------------------------------------------------------
    if real_cuts:
        covered = {m["time"] for m in markers}

        for cut_t in sorted(real_cuts):
            if not (lo <= cut_t <= hi):
                continue
            # Skip if a silence-based candidate already sits within SCENE_RADIUS.
            if any(abs(cut_t - t) <= SCENE_RADIUS for t in covered):
                continue

            # 조건 2: 컷이 두 문장 사이의 간격에 떨어지는지 확인.
            # sentences[k]["end"] <= cut_t <= sentences[k+1]["start"] 인 k가 있어야 함.
            # 단, Whisper 타임스탬프 오차를 감안해 ±CUT_BOUNDARY_WINDOW(0.5s) 여유를 줌.
            # "경계가 근처에 있다"가 아니라 "컷이 문장 간 간격에 속한다"는 엄격한 조건.
            in_gap = any(
                sentences[k]["end"] - CUT_BOUNDARY_WINDOW <= cut_t <=
                sentences[k + 1]["start"] + CUT_BOUNDARY_WINDOW
                for k in range(len(sentences) - 1)
            )
            if not in_gap:
                continue  # 컷이 문장 중간에 있음 → 건너뜀

            # 마커를 컷 시간에서 가장 가까운 허용 프레임(:00/:01/:02/:03/:28/:29)에 배치.
            frame = _nearest_allowed_frame(cut_t)
            if frame is None:
                continue
            marker_time = frame_to_seconds(frame)

            # Find the nearest sentence boundary for text-based scoring.
            # We look within CUT_TEXT_WINDOW seconds; outside that we fall
            # back to generic empty strings so text penalties still apply.
            CUT_TEXT_WINDOW = 8.0
            best_i = min(range(len(sentences) - 1),
                         key=lambda k: min(abs(sentences[k]["end"] - cut_t),
                                           abs(sentences[k + 1]["start"] - cut_t)))
            sent_dist = min(abs(sentences[best_i]["end"] - cut_t),
                            abs(sentences[best_i + 1]["start"] - cut_t))
            if sent_dist <= CUT_TEXT_WINDOW:
                ended_text = sentences[best_i]["text"]
                nxt_text   = sentences[best_i + 1]["text"]
            else:
                ended_text = ""
                nxt_text   = ""

            # Cut-anchor: has_cut=True, cut_dist=0 (컷이 앵커), silence_len=0
            sc, reasons, signal, kill_reason = _score(
                ended_text, nxt_text, frame,
                has_cut=True, cut_dist=0.0, silence_len=0.0)

            # 텍스트 의미 유사도: 낮을수록 주제 전환 가능성 높음.
            text_sim = (text_sims or {}).get(cut_t)
            topic_change = False
            if text_sim is not None:
                if text_sim < TEXT_SIM_THRESHOLD:
                    sc += W_TOPIC_CHANGE
                    topic_change = True

            reason_prefix = "장면 컷 앵커 (CLIP 확인) · Whisper 문장 경계 확인"
            if text_sim is not None:
                sim_tag = f"주제 전환({text_sim:.2f})" if topic_change else f"주제 유지({text_sim:.2f})"
                reason_prefix += f" · {sim_tag}"
            reasons = [reason_prefix] + reasons

            if kill_reason == "continuation" and s.get("exclude_continuation"):
                continue
            if kill_reason == "cta" and s.get("exclude_cta"):
                continue
            if s.get("min_score") is not None and sc < s["min_score"]:
                continue

            m = {
                "time": marker_time,
                "frame": frame,
                "timecode": frame_to_timecode(frame),
                "tier": frame_tier(frame),
                "has_cut": True,
                "has_signal": signal,
                "score": round(sc, 2),
                "reason": "; ".join(reasons),
                "ended_sentence": ended_text,
                "next_sentence": nxt_text,
                "kill_reason": kill_reason,
                "cut_anchor": True,
            }
            if text_sim is not None:
                m["text_sim"] = text_sim
                m["topic_change"] = topic_change
            markers.append(m)
            covered.add(marker_time)

    markers.sort(key=lambda m: m["time"])
    return markers


def pick_primary(markers, duration, settings=None):
    """1st-pass distribution: place ads one after another -- the first in the
    3-10 min range, then each next one 10-15 min after the previous ad.

    The spacing is soft ("10-15분 내외"): if no marker falls in the target
    range, placement looks *forward* and takes the next marker at or after the
    range -- the gap may run past 15 min, but an ad is never pulled closer than
    gap_min to the previous one. An empty stretch never aborts the rest.

    Returns a list of ad slots; each slot's markers are sorted best-first
    (verified transitions, then score) -- index 0 is the recommendation, the
    rest are alternatives the editor can swap in.
    """
    s = {**DEFAULTS, **(settings or {})}
    last = duration - s["outro_deadzone"]
    ms = sorted(markers, key=lambda m: m["time"])
    slots, used = [], set()
    prev = None
    while True:
        if prev is None:
            lo, hi = s["first_min"], s["first_max"]
        else:
            lo, hi = prev + s["gap_min"], prev + s["gap_max"]
        # The earliest a properly-spaced next ad could go (lo) is already past
        # the usable end -- no room left, stop. (Without this the soft fallback
        # would drag a marker back and force an ad too close to the last one.)
        if lo > last:
            break
        avail = [m for m in ms if m["time"] <= last and m["time"] not in used
                 and (prev is None or m["time"] > prev)]
        if not avail:
            break
        in_range = [m for m in avail if lo <= m["time"] <= hi]
        if in_range:
            pool = in_range
        else:
            # Nothing in the ideal 10-15 min window. Never pull an ad closer
            # than gap_min to the previous one -- look forward and take the
            # next marker at or after lo (the gap just runs a bit long). If
            # there is none, there is no room for another ad.
            after = [m for m in avail if m["time"] >= lo]
            if not after:
                break
            pool = [min(after, key=lambda m: m["time"])]
        slot = sorted(pool, key=lambda m: (m["has_cut"], m["score"]),
                      reverse=True)[:s["n_alternatives"]]
        slots.append(slot)
        for m in slot:
            used.add(m["time"])
        prev = slot[0]["time"]
    return slots
