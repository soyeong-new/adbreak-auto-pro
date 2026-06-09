"""전체 분석 파이프라인 조율 (analyzer.py)

영상 한 편에 대해 전체 분석 흐름을 순서대로 실행합니다.

  1. Whisper 음성 전사 / PySceneDetect 장면 탐지 / ffmpeg 음량 곡선 추출
  2. CLIP 배치 검증 — PySceneDetect 컷 전체를 한 번에 검사해 진짜 장면 전환 확정
  3. 텍스트 유사도 — 컷 전후 발화를 ko-sroberta로 임베딩해 주제 전환 여부 측정
  4. 마커 후보 생성 — 침묵 기반(Path 1) + 컷 앵커(Path 2) 두 경로로 후보 생성
  5. CLIP 개별 재검증 — Path 1 마커 단건 확인
  6. 1차 XML (_adbreaks.xml) / 2차 XML (_adbreaks_all.xml) 생성

외부에서는 run_analysis(video_path, settings) 함수만 호출합니다.
"""
import os
from concurrent.futures import ThreadPoolExecutor

from pipeline import (get_duration, get_fps, transcribe, detect_scenes,
                      extract_voice_envelope, extract_loudness_envelope,
                      detect_fade_cuts)
from local_breaks import select_ad_breaks_local, pick_primary, W_SCENE
from scene_verify import is_real_scene_change, batch_scene_similarities, SAME_THRESHOLD
from text_similarity import batch_text_similarities
from xml_output import build_candidate_xml
from framecode import seconds_to_timecode, FPS as DEFAULT_FPS


def _verify(video_path, markers, progress=None):
    """CLIP-verify the transition candidates. A transition that fails CLIP is
    demoted to a reference marker -- never dropped, because the sentence end +
    verified silence still hold; only the "scene cut" claim is withdrawn.
    Reference markers are kept as-is (no CLIP).

    Cut-anchor markers (cut_anchor=True) already passed batch CLIP before
    candidate generation; they are annotated but not re-verified here.
    """
    # fade_anchor 마커는 CLIP 재검증 제외 — 암전 프레임에서 CLIP 유사도는 항상 낮게 나와 무의미함
    need_clip = [m for m in markers
                 if m["has_cut"] and not m.get("clip_preconfirmed")
                 and not m.get("fade_anchor")]
    n_cut = len(need_clip)
    if n_cut and progress:
        progress(f"장면 전환 검수 중... (CLIP, {n_cut}개)")
    kept = []
    for m in markers:
        if not m["has_cut"]:
            kept.append(m)
            continue
        if m.get("fade_anchor"):
            kept.append(m)
            continue
        if m.get("clip_preconfirmed"):
            sim = m.get("clip_similarity")
            if sim is not None:
                m["reason"] += f" · CLIP 배치 통과(유사도 {sim:.2f})"
            kept.append(m)
            continue
        real, sim = is_real_scene_change(video_path, m["time"])
        m["clip_similarity"] = round(sim, 3) if sim is not None else None
        if real:
            if sim is not None:
                m["reason"] += f" · CLIP 검수 통과(유사도 {sim:.2f})"
        else:
            m["has_cut"] = False
            m["score"] = round(m["score"] - W_SCENE, 2)  # W_SCENE(5.0) 환수 → 총 패널티 -5.0
            sim_txt = f" 유사도 {sim:.2f}" if sim is not None else ""
            m["reason"] = f"[장면 전환 아님 — CLIP{sim_txt}] " + m["reason"]
        kept.append(m)
    kept.sort(key=lambda m: m["time"])
    return kept


def run_analysis(video_path, settings=None, progress=None):
    """Full analysis. Returns a report dict (JSON-serializable)."""
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    duration = get_duration(video_path)
    detected_fps = get_fps(video_path)

    # fps_mode: "auto" → 영상 FPS 그대로, 숫자 문자열/float → 강제 지정.
    # watcher는 설정값 없이 호출 → 기본 30fps 고정.
    fps_mode = (settings or {}).get("fps_mode", "30")
    if fps_mode == "auto":
        fps = detected_fps or DEFAULT_FPS
    else:
        try:
            fps = float(fps_mode)
        except (TypeError, ValueError):
            fps = DEFAULT_FPS

    # Stages 2-4 (자막 변환 · 장면 감지 · 음성 분석) are independent — they each
    # read the video on their own and don't use each other's output, so we run
    # them concurrently. Results are identical to running them in order.
    if progress:
        progress("자막 변환 · 장면 감지 · 음성 분석 · 페이드 탐지 (병렬 처리 중)...")
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_segments = pool.submit(transcribe, video_path, progress)
        f_scenes = pool.submit(detect_scenes, video_path, progress)
        f_voice    = pool.submit(extract_voice_envelope,    video_path, progress)
        f_loudness = pool.submit(extract_loudness_envelope, video_path, progress)
        f_fades    = pool.submit(detect_fade_cuts,          video_path, progress)
        segments  = f_segments.result()
        scenes    = f_scenes.result()
        voice     = f_voice.result()
        loudness  = f_loudness.result()
        fades     = f_fades.result()

    # Batch CLIP: confirm which scene cuts are genuine transitions (cached).
    # Used to upgrade silence-based markers within SCENE_RADIUS_CLIP of a
    # confirmed cut to has_cut=True without generating new candidates.
    _s = {**{"intro_deadzone": 180.0, "outro_deadzone": 180.0},
          **(settings or {})}
    valid_cuts = [c for c in scenes
                  if _s["intro_deadzone"] <= c <= duration - _s["outro_deadzone"]]
    # 장르별 CLIP 문턱 — 이 값 미만이면 "진짜 장면 전환"으로 컷 앵커 후보 생성.
    # 기본 0.80(SAME_THRESHOLD). 자취남처럼 같은 공간 내 약한 컷이 광고점인 장르는
    # 0.85로 완화해 후보를 넓힌다 (genres.json clip_threshold).
    clip_th = float(_s.get("clip_threshold", SAME_THRESHOLD))
    clip_sims = {}
    clip_real_cuts = set()
    if valid_cuts:
        clip_sims = batch_scene_similarities(video_path, valid_cuts,
                                             progress=progress)
        clip_real_cuts = {c for c, sim in clip_sims.items()
                          if sim is not None and sim < clip_th}

    # 텍스트 의미 유사도: CLIP 확인된 컷 전후 주제가 바뀌는지 측정.
    # 낮은 유사도 = 주제 전환 = 광고 후보로 우선 고려.
    text_sims = {}
    if valid_cuts:
        text_sims = batch_text_similarities(video_path, segments, valid_cuts,
                                            progress=progress)

    if progress:
        progress("광고 지점 후보 탐색 중...")
    markers = select_ad_breaks_local(segments, duration, settings,
                                     scene_cuts=scenes, voice_env=voice,
                                     loudness_env=loudness,
                                     clip_real_cuts=clip_real_cuts,
                                     text_sims=text_sims,
                                     fade_cuts=fades,
                                     fps=fps)

    # Attach batch CLIP similarity to clip_preconfirmed markers.
    for m in markers:
        if m.get("clip_preconfirmed") and clip_sims:
            nearest_cut = min(clip_sims, key=lambda c: abs(c - m["time"]))
            if abs(nearest_cut - m["time"]) < 1.5:
                m["clip_similarity"] = clip_sims[nearest_cut]

    markers = _verify(video_path, markers, progress)

    # 1st pass: ad slots (each = recommendation + alternatives in its window).
    # 2nd pass: CLIP-confirmed cut-anchor markers only, no gap_min/spacing limit.
    #   These are positions where a genuine scene cut (CLIP similarity < SAME_THRESHOLD)
    #   coincides with a sentence boundary — the raw material the editor reviews
    #   to make the final ad break decision.
    primary_slots = pick_primary(markers, duration, settings)
    primary_flat = [m for slot in primary_slots for m in slot]
    prim_times = {m["time"] for m in primary_flat}
    for m in markers:
        m["primary"] = m["time"] in prim_times

    # 2nd pass: cut-anchor only (Path 2) — CLIP-verified scene cut + sentence boundary.
    # Sorted by time, no spacing constraint.
    cut_anchor_markers = [m for m in markers if m.get("cut_anchor")]

    return {
        "video_path": video_path,
        "video_name": os.path.basename(video_path),
        "duration": duration,
        "duration_tc": seconds_to_timecode(duration, fps),
        "fps": round(fps, 3),
        "fps_detected": round(detected_fps, 3) if detected_fps else None,
        "segments_count": len(segments),
        "scenes_count": len(scenes),
        "marker_count": len(markers),
        "primary_count": len(primary_slots),
        "transition_count": sum(1 for m in markers if m["has_cut"]),
        "reference_count": sum(1 for m in markers if not m["has_cut"]),
        "primary_slots": primary_slots,
        "markers": markers,
        "xml_primary": build_candidate_xml(primary_flat, video_path, duration, fps),
        "xml_all": build_candidate_xml(cut_anchor_markers, video_path, duration, fps),
    }
