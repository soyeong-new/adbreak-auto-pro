"""Ad break analysis pipeline.

Transcribe the video, detect scene cuts and the voice envelope, find every
criteria-meeting marker, verify each with CLIP, then render Premiere-importable
marker XMLs for both the 1st-pass (spaced subset) and 2nd-pass (all markers).
"""
import os
from concurrent.futures import ThreadPoolExecutor

from pipeline import (get_duration, get_fps, transcribe, detect_scenes,
                      extract_voice_envelope)
from local_breaks import select_ad_breaks_local, pick_primary, W_SCENE
from scene_verify import is_real_scene_change, batch_scene_similarities, SAME_THRESHOLD
from text_similarity import batch_text_similarities
from xml_output import build_candidate_xml
from framecode import seconds_to_timecode


def _verify(video_path, markers, progress=None):
    """CLIP-verify the transition candidates. A transition that fails CLIP is
    demoted to a reference marker -- never dropped, because the sentence end +
    verified silence still hold; only the "scene cut" claim is withdrawn.
    Reference markers are kept as-is (no CLIP).

    Cut-anchor markers (cut_anchor=True) already passed batch CLIP before
    candidate generation; they are annotated but not re-verified here.
    """
    need_clip = [m for m in markers
                 if m["has_cut"] and not m.get("clip_preconfirmed")]
    n_cut = len(need_clip)
    if n_cut and progress:
        progress(f"장면 전환 검수 중... (CLIP, {n_cut}개)")
    kept = []
    for m in markers:
        if not m["has_cut"]:
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
            m["score"] = round(m["score"] - W_SCENE, 2)
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
    fps = get_fps(video_path)

    # Stages 2-4 (자막 변환 · 장면 감지 · 음성 분석) are independent — they each
    # read the video on their own and don't use each other's output, so we run
    # them concurrently. Results are identical to running them in order.
    if progress:
        progress("자막 변환 · 장면 감지 · 음성 분석 (병렬 처리 중)...")
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_segments = pool.submit(transcribe, video_path, progress)
        f_scenes = pool.submit(detect_scenes, video_path, progress)
        f_voice = pool.submit(extract_voice_envelope, video_path, progress)
        segments = f_segments.result()
        scenes = f_scenes.result()
        voice = f_voice.result()

    # Batch CLIP: confirm which scene cuts are genuine transitions (cached).
    # Used to upgrade silence-based markers within SCENE_RADIUS_CLIP of a
    # confirmed cut to has_cut=True without generating new candidates.
    _s = {**{"intro_deadzone": 180.0, "outro_deadzone": 180.0},
          **(settings or {})}
    valid_cuts = [c for c in scenes
                  if _s["intro_deadzone"] <= c <= duration - _s["outro_deadzone"]]
    clip_sims = {}
    clip_real_cuts = set()
    if valid_cuts:
        clip_sims = batch_scene_similarities(video_path, valid_cuts,
                                             progress=progress)
        clip_real_cuts = {c for c, sim in clip_sims.items()
                          if sim is not None and sim < SAME_THRESHOLD}

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
                                     clip_real_cuts=clip_real_cuts,
                                     text_sims=text_sims)

    # Attach batch CLIP similarity to clip_preconfirmed markers.
    for m in markers:
        if m.get("clip_preconfirmed") and clip_sims:
            nearest_cut = min(clip_sims, key=lambda c: abs(c - m["time"]))
            if abs(nearest_cut - m["time"]) < 1.5:
                m["clip_similarity"] = clip_sims[nearest_cut]

    markers = _verify(video_path, markers, progress)

    # 1st pass: ad slots (each = recommendation + alternatives in its window).
    # 2nd pass: every marker, no distance/count limit.
    primary_slots = pick_primary(markers, duration, settings)
    primary_flat = [m for slot in primary_slots for m in slot]
    prim_times = {m["time"] for m in primary_flat}
    for m in markers:
        m["primary"] = m["time"] in prim_times

    return {
        "video_path": video_path,
        "video_name": os.path.basename(video_path),
        "duration": duration,
        "duration_tc": seconds_to_timecode(duration),
        "fps": round(fps, 3) if fps else None,
        "segments_count": len(segments),
        "scenes_count": len(scenes),
        "marker_count": len(markers),
        "primary_count": len(primary_slots),
        "transition_count": sum(1 for m in markers if m["has_cut"]),
        "reference_count": sum(1 for m in markers if not m["has_cut"]),
        "primary_slots": primary_slots,
        "markers": markers,
        "xml_primary": build_candidate_xml(primary_flat, video_path, duration),
        "xml_all": build_candidate_xml(markers, video_path, duration),
    }
