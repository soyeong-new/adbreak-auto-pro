"""마커 후보 생성 및 점수 계산 핵심 로직 (local_breaks.py)

두 가지 경로로 마커 후보를 생성합니다.

  Path 1 — 침묵 기반
    문장 종결 직후 적응형 침묵(≥0.5s)이 확인되고, 침묵 안에 허용 프레임이 있는 경우.
    ±1.0s 이내 가장 가까운 컷을 CLIP 배치검증 결과로 분류해 has_cut을 판정
    (진짜 확인됨/가짜로 확인됨/아직 확인 안 됨 3가지, _classify_scene_transition 참조).

  Path 2 — 컷 앵커
    CLIP 유사도 < 0.80인 실제 화면 전환이 허용 프레임에 정확히 착지하고,
    Whisper 문장/세그먼트 갭이 ±0.5s 이내에 있는 경우. 침묵 불필요.

출력 두 가지 (analyzer.py에서 조립):
  1차 (_adbreaks.xml)    : 간격 규칙 적용(첫 광고 3~10분, 이후 10~15분). 슬롯별 상위 최대 5개.
  2차 (_adbreaks_all.xml): Path 1/2/3 전체 후보, 간격 제한 없이 전체. 1차는 항상 이 부분집합.

주요 함수:
  select_ad_breaks_local() — 마커 후보 생성 전체
  pick_primary()           — 1차 배치 선발
"""
import re
import math
from topic_breaks import build_sentences
from framecode import (FPS, FF_TOP, _base,
                       frame_to_seconds, frame_tier, frame_to_timecode,
                       _df_frame_ff)
from patterns import starts_with_continuation

DEFAULTS = {
    "intro_deadzone": 180.0,
    "outro_deadzone": 180.0,
    "first_min": 180.0,
    "first_max": 600.0,
    "gap_min": 600.0,
    "gap_max": 900.0,
    "n_alternatives": 5,       # markers shown per 1st-pass ad slot
    "w_quiet_cut": 0.0,         # 컷·침묵이 조용한 구간(BGM 없음 추정)에 있을 때 가산점
    # --- v1.1 heuristic toggles. Defaults preserve v1.0 behavior. ---
    # When True, a sentence pair where the next sentence opens with a
    # continuation marker ("근데/사실/그리고/아/음/…") is dropped. A penalty
    # of P_CONTINUATION is always applied (whether or not the marker is kept).
    "exclude_continuation": False,
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
W_SCENE = 8.0
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

# Fade-anchor path (Path 3): 페이드 인/아웃 기반 마커 탐지.
# V 꼭짓점(가장 어두운 프레임) 시각 ±FADE_SILENCE_SEARCH 범위에서 침묵을 확인.
# 침묵 기준은 Path 1보다 낮은 0.2s — 페이드 아웃 중 음성이 완전히 꺼지는 짧은 구간도 잡기 위함.
FADE_SILENCE_SEARCH = 0.5  # V 꼭짓점 전후 침묵 탐색 반경(s)
FADE_SILENCE_MIN    = 0.2  # 침묵 인정 최소 길이(s) — Path 1(≥0.5s)보다 느슨하게
W_FADE = 4.0               # 페이드 앵커 장면 전환 가중치 기본값 (UI에서 override 가능)


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


def _get_ff(frame, fps, drop_frame):
    """프레임의 FF(초 안 위치) 반환. DF면 SMPTE DF 공식 사용."""
    if drop_frame and abs(fps - 29.97) < 0.1:
        return _df_frame_ff(frame)
    return frame % _base(fps)


def _allowed_frame_in(s0, s1, fps=FPS, drop_frame=False):
    """침묵 [s0, s1] 안의 허용 프레임 반환. :00 우선, 없으면 :01~:03/끝 2프레임."""
    f0 = math.ceil(s0 * fps)
    f1 = math.floor(s1 * fps)
    b       = _base(fps)
    ff_cand = {1, 2, 3, b - 2, b - 1}
    cand = None
    for f in range(f0, f1 + 1):
        ff = _get_ff(f, fps, drop_frame)
        if ff in FF_TOP:
            return f
        if cand is None and ff in ff_cand:
            cand = f
    return cand


def _nearest_allowed_frame(t, fps=FPS, drop_frame=False):
    """장면 컷 시간 t가 허용 프레임에 정확히 해당할 때만 반환. 스냅 없음."""
    f_center = int(round(t * fps))
    ff = _get_ff(f_center, fps, drop_frame)
    b  = _base(fps)
    if ff in FF_TOP | {1, 2, 3, b - 2, b - 1}:
        return f_center
    return None


def _classify_scene_transition(marker_time, cuts, real_cuts, checked_cuts):
    """마커 시각에서 가장 가까운 원본 컷을 찾아 화면전환 여부를 판정한다.

    real_cuts   : CLIP 배치검증에서 진짜 장면전환으로 확인된 컷 집합.
    checked_cuts: CLIP 배치검증을 시도해서 값이 나온 컷 전체 집합(real_cuts의 상위집합).
                  real_cuts에 없지만 checked_cuts엔 있으면 "확인했는데 가짜"라는 뜻이고,
                  checked_cuts에도 없으면 "배치검증 자체가 안 됨"(데드존 경계 등)이라는 뜻이다.

    Returns (has_cut, cut_dist, clip_preconfirmed).
    """
    if not cuts:
        return False, 0.0, False
    cut = min(cuts, key=lambda c: abs(c - marker_time))
    dist = abs(cut - marker_time)
    if dist > SCENE_RADIUS_CLIP:
        return False, 0.0, False
    if cut in real_cuts:
        return True, dist, True
    if cut in checked_cuts:
        return False, 0.0, False
    return True, dist, False


def _score(ended, nxt, frame, has_cut, cut_dist, silence_len,
           w_scene=W_SCENE, fade_mode=False, fps=FPS, drop_frame=False):
    """Score a marker. Returns (score, reasons, has_signal, kill_reason).

    Every marker already sits in a verified silence after a completed sentence;
    this only ranks them. has_signal: a discourse cue (a topic-shift opener on
    the next sentence, or a closing phrase on the ended one) is present.
    kill_reason: a short tag (str) when this pair matches a v1.1 exclude
    pattern, else None. The caller decides whether to drop the marker based on
    its settings — `_score` itself never drops anything.

    w_scene   : 장면/페이드 전환 보너스 (UI 장르 설정에 따라 override)
    fade_mode : Path 3(페이드) 전용. 페이드 구간은 발화가 끊겨 대사 기반 점수가
                노이즈이므로, 화면·프레임·마무리 표현만 채점하고 나머지 대사
                항목(연속/전환 표현, 짧은 문장, Q&A)은 건너뛴다.

    2026-06: CTA 패널티(p_cta) 전면 삭제 — 측정 결과 정답 재현에 영향이 없어
    전체 패스·전체 장르에서 제거. patterns.has_cta는 더 이상 채점에 쓰지 않음.
    """
    score = 0.0
    reasons = [f"문장 끝 직후 {silence_len:.1f}초 침묵"]
    signal = False
    kill_reason = None

    if has_cut:
        score += w_scene
        reasons.append(f"장면 컷에서 시작({cut_dist:.2f}s)")

    nxt_s = nxt.strip()
    ended_core = _strip_punct(ended)

    # 대사 기반 항목 — 페이드 구간은 발화 단절로 신뢰 불가, fade_mode에서 건너뜀.
    if not fade_mode:
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

    # 마무리 표현 — Path 1/2/3 모두 적용. 페이드아웃 직전 문장이 종결 표현이면
    # "장면이 끝나고 암전" 패턴이라 페이드에서도 유효한 신호.
    if any(ended_core.endswith(c) for c in CLOSERS):
        score += W_CLOSER
        reasons.append("앞 문장이 마무리 표현으로 종료")
        signal = True

    if silence_len >= LONG_SILENCE:
        score += W_PAUSE
        reasons.append(f"긴 침묵({silence_len:.1f}초)")

    if _get_ff(frame, fps, drop_frame) == 0:
        score += W_FRAME_TOP
        reasons.append("최우선 :00 프레임")

    if not fade_mode:
        if len(ended_core) < 8:
            score -= P_SHORT
            reasons.append("앞 문장이 너무 짧음(조각)")
        if ended.strip().endswith("?") or re.search(r"(냐|까|까요|나요)$", ended_core):
            score -= P_QA
            reasons.append("앞 문장이 질문(자문자답 중간 가능성)")

    return score, reasons, signal, kill_reason


def select_ad_breaks_local(segments, duration, settings=None,
                           scene_cuts=None, voice_env=None,
                           loudness_env=None,
                           clip_real_cuts=None, clip_checked_cuts=None,
                           text_sims=None,
                           fade_cuts=None, fps=FPS, drop_frame=False):
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
    checked_cuts = set(clip_checked_cuts) if clip_checked_cuts else set()

    lo = s["intro_deadzone"]
    hi = duration - s["outro_deadzone"]

    # 장르 가중치 — UI 설정값이 있으면 우선 사용, 없으면 모듈 상수 기본값
    _w_scene  = float(s.get("w_scene",        W_SCENE))
    _w_topic  = float(s.get("w_topic_change", W_TOPIC_CHANGE))
    _sil_min  = float(s.get("silence_min",    SILENCE_MIN))
    _w_fade   = float(s.get("w_fade",         W_FADE))
    # Path 3(페이드) 장르 파라미터
    #   fade_require_silence: 침묵을 관문으로 요구할지. 영화·드라마·케이팝은 False
    #     (페이드 위 배경 스코어가 지속돼 침묵이 없어도 진짜 전환임).
    #   fade_silence_bonus: 침묵이 동반될 때 주는 가산점. 케이팝만 >0
    #     (다른 가수 등장 시 페이드인 + 침묵이 강한 신호).
    _fade_req_sil = bool(s.get("fade_require_silence", True))
    _fade_sil_bonus = float(s.get("fade_silence_bonus", 0.0))
    _w_quiet_cut = float(s.get("w_quiet_cut", 0.0))

    # the video's own noise floor -- bottom of the adaptive silence scale
    noise_floor = (_noise_floor(voice_env)
                   if voice_env and voice_env.get("db") else -70.0)

    markers = []

    # ------------------------------------------------------------------
    # Path 1 (original): sentence boundary + verified silence
    # ------------------------------------------------------------------
    for i in range(len(sentences) - 1):
        ended, nxt = sentences[i], sentences[i + 1]

        # 데드존 조기 스킵: 침묵 탐색 범위 전체가 데드존 밖이면 값싼 비교만으로
        # 걸러내고, 뒤쪽의 무거운 침묵 탐색(_find_silence)을 건너뛴다. 범위가
        # 걸쳐 있는 경우는 여기서 걸러지지 않고 통과 -- 최종 정밀 판정은
        # marker_time 확정 후 아래(관문 3)에서 그대로 수행한다.
        if nxt["start"] + SILENCE_SEARCH < lo or ended["end"] - SILENCE_SEARCH > hi:
            continue

        # A real silence must follow the completed sentence -- otherwise the
        # speech runs straight through and this is not a true sentence break.
        sil = _find_silence(voice_env, ended["end"] - SILENCE_SEARCH,
                            nxt["start"] + SILENCE_SEARCH, noise_floor,
                            min_dur=_sil_min)
        if sil is None:
            continue
        # Place the marker on an allowed frame *inside* that silence.
        frame = _allowed_frame_in(*sil, fps=fps, drop_frame=drop_frame)
        if frame is None:                      # silence holds no allowed frame
            continue
        marker_time = frame_to_seconds(frame, fps)
        if not (lo <= marker_time <= hi):
            continue

        # A scene cut coinciding with the silence -> transition candidate.
        # Find the nearest original cut within SCENE_RADIUS_CLIP and classify it
        # against the CLIP batch-verification results: confirmed real (has_cut=True,
        # clip_preconfirmed=True, skip later individual re-verification), confirmed
        # fake (has_cut=False), or not yet checked (has_cut=True, deferred to
        # individual re-verification later). See _classify_scene_transition().
        has_cut, cut_dist, clip_preconfirmed = _classify_scene_transition(
            marker_time, cuts, real_cuts, checked_cuts)

        tier = frame_tier(frame, fps, drop_frame)
        sc, reasons, signal, kill_reason = _score(
            ended["text"], nxt["text"], frame, has_cut, cut_dist,
            sil[1] - sil[0],
            w_scene=_w_scene, fps=fps, drop_frame=drop_frame)

        # Path 1 조용한 구간 보너스: 침묵 마커 위치에서 loudness_env(전체주파수)도
        # 낮으면 BGM도 없는 것으로 판단해 가산점. loudness 없으면 voice_env 사용.
        path1_quiet = False
        if _w_quiet_cut:
            env = loudness_env if (loudness_env and loudness_env.get("db")) \
                  else (voice_env if (voice_env and voice_env.get("db")) else None)
            if env:
                loud_nf = _noise_floor(env)
                # 침묵 마커 자체가 이미 조용한 구간이지만 loudness 기준으로 재확인
                loud_sil = _find_silence(env, marker_time - 1.0, marker_time + 1.0,
                                         loud_nf, min_dur=0.3)
                if loud_sil is not None:
                    sc += _w_quiet_cut
                    path1_quiet = True

        # v1.1 exclusion: optional, off by default. exclude_* tells the
        # detector to drop markers whose kill_reason matches.
        if kill_reason == "continuation" and s.get("exclude_continuation"):
            continue
        # v1.1 minimum score cutoff: optional, off by default.
        if s.get("min_score") is not None and sc < s["min_score"]:
            continue

        m = {
            "time": marker_time,
            "frame": frame,
            "timecode": frame_to_timecode(frame, fps, drop_frame),
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
        if path1_quiet:
            m["quiet_cut"] = True
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

            # 조건 2: 컷이 발화 갭에 떨어지는지 확인.
            # build_sentences는 문법 기반 병합이라 Whisper 원본 갭이 묻힐 수 있음.
            # 따라서 (A) 합쳐진 sentences 기준 갭, (B) 원본 segments 기준 갭 둘 다 확인.
            # 하나라도 해당하면 허용. 단, ±CUT_BOUNDARY_WINDOW(0.5s) 여유를 줌.
            in_gap = any(
                sentences[k]["end"] - CUT_BOUNDARY_WINDOW <= cut_t <=
                sentences[k + 1]["start"] + CUT_BOUNDARY_WINDOW
                for k in range(len(sentences) - 1)
            ) or any(
                segments[k]["end"] - CUT_BOUNDARY_WINDOW <= cut_t <=
                segments[k + 1]["start"] + CUT_BOUNDARY_WINDOW
                for k in range(len(segments) - 1)
            )
            if not in_gap:
                continue  # 컷이 문장/세그먼트 중간에 있음 → 건너뜀

            # 마커를 컷 시간에서 가장 가까운 허용 프레임(:00/:01/:02/:03/:28/:29)에 배치.
            frame = _nearest_allowed_frame(cut_t, fps, drop_frame)
            if frame is None:
                continue
            marker_time = frame_to_seconds(frame, fps)

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
                has_cut=True, cut_dist=0.0, silence_len=0.0,
                w_scene=_w_scene, fps=fps, drop_frame=drop_frame)

            # 텍스트 의미 유사도: 낮을수록 주제 전환 가능성 높음.
            text_sim = (text_sims or {}).get(cut_t)
            topic_change = False
            if text_sim is not None:
                if text_sim < TEXT_SIM_THRESHOLD:
                    sc += _w_topic
                    topic_change = True

            # 조용한 컷 보너스: 전체 주파수 loudness가 낮으면 BGM 없음으로 판단.
            # loudness_env(전체 대역) vs voice_env(250~3000Hz 음성 대역)을 비교해
            # 음성은 없는데 BGM이 있으면 loudness가 높게 나오는 원리.
            quiet_cut = False
            if _w_quiet_cut:
                env = loudness_env if (loudness_env and loudness_env.get("db")) \
                      else (voice_env if (voice_env and voice_env.get("db")) else None)
                if env:
                    loud_nf = _noise_floor(env)
                    sil = _find_silence(env, marker_time - 2.0, marker_time + 2.0,
                                        loud_nf, min_dur=0.5)
                    if sil is not None:
                        sc += _w_quiet_cut
                        quiet_cut = True

            reason_prefix = "장면 컷 앵커 (CLIP 확인) · Whisper 문장 경계 확인"
            if text_sim is not None:
                sim_tag = f"주제 전환({text_sim:.2f})" if topic_change else f"주제 유지({text_sim:.2f})"
                reason_prefix += f" · {sim_tag}"
            reasons = [reason_prefix] + reasons

            if kill_reason == "continuation" and s.get("exclude_continuation"):
                continue
            if s.get("min_score") is not None and sc < s["min_score"]:
                continue

            m = {
                "time": marker_time,
                "frame": frame,
                "timecode": frame_to_timecode(frame, fps, drop_frame),
                "tier": frame_tier(frame, fps, drop_frame),
                "has_cut": True,
                "has_signal": signal,
                "score": round(sc, 2),
                "reason": "; ".join(reasons),
                "ended_sentence": ended_text,
                "next_sentence": nxt_text,
                "kill_reason": kill_reason,
                "cut_anchor": True,
                "clip_preconfirmed": True,
            }
            if text_sim is not None:
                m["text_sim"] = text_sim
                m["topic_change"] = topic_change
            if quiet_cut:
                m["quiet_cut"] = True
            markers.append(m)
            covered.add(marker_time)

    # ------------------------------------------------------------------
    # Path 3 (fade-anchor): 페이드 인/아웃 V 꼭짓점 기반 마커.
    #
    # 조건:
    #   1. 화면 암전 (ffmpeg 밝기 분석으로 탐지된 V 꼭짓점)
    #   2. 음성 침묵 (V 꼭짓점 ±FADE_SILENCE_SEARCH 범위, 최소 FADE_SILENCE_MIN 초)
    #   3. 허용 프레임 — 침묵 안에 :00/:01~:03/:28~:29 프레임 존재
    #   4. 데드존 외부 (lo ≤ 마커 시각 ≤ hi)
    #   5. Path 1/2 마커와 SCENE_RADIUS 이내 중복 시 건너뜀
    #
    # CLIP 검증 없음 — 암전 프레임에서는 CLIP 유사도가 항상 낮게 나와 의미 없음.
    # 텍스트 의미 유사도 없음 — 페이드 중에는 발화가 끊겨 있어 임베딩 신뢰도 낮음.
    # ------------------------------------------------------------------
    if fade_cuts:
        # 컷·페이드 중복은 '페이드 우선 + 증거 병합'으로 처리한다.
        # existing: 병합 대상(Path 1/2 마커). placed_fades: 페이드끼리 중복 방지용.
        existing = list(markers)
        placed_fades = set()

        for fade_t in sorted(fade_cuts):
            if not (lo <= fade_t <= hi):
                continue
            # (1) 컷·페이드 중복: SCENE_RADIUS 이내 기존 Path 1/2 마커가 있으면,
            #     새 마커를 만들지 않고 그 마커를 '페이드 앵커'로 승격(증거 병합).
            #     → CLIP 재검증 면제 + 2차 XML 포함. 컷의 CLIP·점수 증거는 유지하고
            #     w_fade를 가산 — 컷/침묵과 페이드 증거가 동시에 있으면 더 강한 신호.
            overlap = [m for m in existing
                       if abs(fade_t - m["time"]) <= SCENE_RADIUS]
            if overlap:
                m = min(overlap, key=lambda m: abs(fade_t - m["time"]))
                if not m.get("fade_anchor"):
                    m["fade_anchor"] = True
                    m["cut_anchor"] = True
                    m["score"] = round(m["score"] + _w_fade, 2)
                    m["reason"] += (f"; 페이드 V 꼭짓점 겹침({fade_t:.2f}s)"
                                    f" — 페이드 우선·증거 병합(+{_w_fade:.1f})")
                continue
            # (2) 페이드끼리 SCENE_RADIUS 이내 중복이면 건너뜀
            if any(abs(fade_t - t) <= SCENE_RADIUS for t in placed_fades):
                continue

            # 침묵 확인: V 꼭짓점 ±FADE_SILENCE_SEARCH 범위에서 FADE_SILENCE_MIN 이상 침묵.
            #   fade_require_silence=True (토크·강의 등): 침묵이 관문 — 없으면 탈락.
            #   False (영화·드라마·케이팝): 페이드 위 배경 스코어가 지속돼 침묵이 없어도
            #     진짜 전환이므로 통과. 침묵은 있으면 보너스로만 반영.
            sil = _find_silence(voice_env,
                                max(0.0, fade_t - FADE_SILENCE_SEARCH),
                                fade_t + FADE_SILENCE_SEARCH,
                                noise_floor,
                                min_dur=FADE_SILENCE_MIN)
            if sil is None and _fade_req_sil:
                continue

            # 프레임 배치: 침묵이 있으면 그 침묵 안에, 없으면 V 꼭짓점 최근접 허용 프레임.
            if sil is not None:
                frame = _allowed_frame_in(*sil, fps=fps, drop_frame=drop_frame)
                silence_len = sil[1] - sil[0]
            else:
                frame = _nearest_allowed_frame(fade_t, fps, drop_frame)
                silence_len = 0.0
            if frame is None:
                continue
            marker_time = frame_to_seconds(frame, fps)
            if not (lo <= marker_time <= hi):
                continue

            # 인접 문장 텍스트 (점수 계산용, 없으면 빈 문자열)
            CUT_TEXT_WINDOW = 8.0
            if len(sentences) >= 2:
                best_i = min(range(len(sentences) - 1),
                             key=lambda k: min(abs(sentences[k]["end"] - fade_t),
                                               abs(sentences[k + 1]["start"] - fade_t)))
                sent_dist = min(abs(sentences[best_i]["end"] - fade_t),
                                abs(sentences[best_i + 1]["start"] - fade_t))
                if sent_dist <= CUT_TEXT_WINDOW:
                    ended_text = sentences[best_i]["text"]
                    nxt_text   = sentences[best_i + 1]["text"]
                else:
                    ended_text = ""
                    nxt_text   = ""
            else:
                ended_text = ""
                nxt_text   = ""

            # has_cut=True (화면 전환 신호로 취급), CLIP 재검증 없음.
            # fade_mode=True — 대사 기반 점수는 제외, 화면·프레임·마무리 표현만 채점.
            sc, reasons, signal, kill_reason = _score(
                ended_text, nxt_text, frame,
                has_cut=True, cut_dist=0.0,
                silence_len=silence_len,
                w_scene=_w_fade, fade_mode=True, fps=fps, drop_frame=drop_frame)

            # 케이팝 등: 페이드 + 침묵 동반 시 가산 (다른 가수 등장 페이드인 신호).
            if sil is not None and _fade_sil_bonus:
                sc += _fade_sil_bonus
                reasons.append(f"페이드 + 침묵 동반(+{_fade_sil_bonus:.1f})")

            sil_tag = "음성 침묵 확인" if sil is not None else "침묵 없음(배경음 지속)"
            reasons = [f"페이드 인/아웃 V 꼭짓점 ({fade_t:.2f}s) · {sil_tag}"] + reasons

            if kill_reason == "continuation" and s.get("exclude_continuation"):
                continue
            if s.get("min_score") is not None and sc < s["min_score"]:
                continue

            m = {
                "time": marker_time,
                "frame": frame,
                "timecode": frame_to_timecode(frame, fps, drop_frame),
                "tier": frame_tier(frame, fps, drop_frame),
                "has_cut": True,
                "has_signal": signal,
                "score": round(sc, 2),
                "reason": "; ".join(reasons),
                "ended_sentence": ended_text,
                "next_sentence": nxt_text,
                "kill_reason": kill_reason,
                "fade_anchor": True,        # CLIP 재검증 제외 플래그
                "cut_anchor": True,         # 2차 XML 포함용 플래그
            }
            markers.append(m)
            placed_fades.add(marker_time)

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
                 and (prev is None or m["time"] > prev)]  # 침묵+문장 경계 전체 포함
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
