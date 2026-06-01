"""장르 설정 변경의 정답 재현율을 측정한다 — 캐시된 feature로 영상 재처리 없이.

genre-tuning 스킬의 측정 도구. (삭제된 measure_* 일회용들과 달리 장르
파라미터화된 영구 도구 — 어떤 장르든 같은 방식으로 잰다.)

핵심 규칙: 정답 시각마다 출력 마커가 ±TOL초 안에 있으면 hit. 한 에피소드에
맞추지 말고 장르 전체 에피소드 집합으로 잰다(과적합 방지).

사용:
  # 현재 genres.json 설정으로 측정
  ../.venv/bin/python eval/measure_recall.py 자취남

  # 설정 하나만 바꿔 A/B 비교 (현재값 vs override)
  ../.venv/bin/python eval/measure_recall.py 자취남 --set clip_threshold=0.80

  # 여러 개 동시 override
  ../.venv/bin/python eval/measure_recall.py 자취남 --set clip_threshold=0.82 --set w_topic_change=8
"""
import os
import sys
import glob
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from local_breaks import select_ad_breaks_local, pick_primary  # noqa: E402
from scene_verify import SAME_THRESHOLD  # noqa: E402

CACHE = os.path.join(ROOT, ".cache")
TOL = 5.0  # 정답 ±5초 안에 마커가 있으면 hit (measure_regression.py 기준 유지)

# 분 단위(×60) / 그대로(float) / bool — app.py 의 변환 규칙과 동일
MIN_KEYS = ("first_min", "first_max", "gap_min", "gap_max",
            "intro_deadzone", "outro_deadzone")
FLOAT_KEYS = ("w_scene", "w_topic_change", "w_fade", "fade_silence_bonus",
              "clip_threshold", "silence_min")
BOOL_KEYS = ("fade_require_silence",)

# 장르 → 정답 소스. 새 장르 정답이 들어오면 여기 한 줄 추가.
GT_TXT = {"자취남": "jcn_ground_truth.txt", "home": "jcn_ground_truth.txt"}


def hms_to_sec(s):
    parts = [int(p) for p in s.strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, sec = parts
    return h * 3600 + m * 60 + sec


def load_gt_txt(path):
    """'EPISODE  HH:MM:SS, HH:MM:SS' 형식 → {episode: [seconds]}."""
    gt = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ep, _, times = line.partition(" ")
        secs = [hms_to_sec(t) for t in times.split(",") if t.strip()]
        if secs:
            gt[ep] = secs
    return gt


def load_gt_json(path):
    """ground_truth.json {ground_truth:{ep:[sec]}, resolved:{ep:filename}}."""
    d = json.load(open(path, encoding="utf-8"))
    gt, resolved = {}, {}
    for ep, secs in d["ground_truth"].items():
        fn = d.get("resolved", {}).get(ep)
        if fn and secs:
            gt[ep] = secs
            resolved[ep] = fn
    return gt, resolved


def find_genre(label):
    genres = json.load(open(os.path.join(ROOT, "genres.json"), encoding="utf-8"))
    for g in genres:
        if label in (g.get("label"), g.get("id"), g.get("folder")):
            return g
    raise SystemExit(f"genres.json 에 '{label}' 장르가 없습니다.")


def build_settings(genre, overrides):
    """genres.json 항목 + override → run_analysis 가 받는 settings 딕셔너리."""
    src = {**genre, **overrides}
    s = {}
    for k in MIN_KEYS:
        if k in src and src[k] is not None:
            s[k] = float(src[k]) * 60.0
    for k in FLOAT_KEYS:
        if k in src and src[k] is not None:
            s[k] = float(src[k])
    for k in BOOL_KEYS:
        if k in src and src[k] is not None:
            s[k] = bool(src[k])
    # run_analysis 기본 deadzone (genres.json 엔 없음)
    s.setdefault("intro_deadzone", 180.0)
    s.setdefault("outro_deadzone", 180.0)
    return s


def stem_for(episode, resolved):
    """에피소드 → 캐시 stem 경로 (없으면 None)."""
    prefix = resolved.get(episode, episode) if resolved else episode
    hits = sorted(glob.glob(f"{CACHE}/{prefix}*.transcript2.json"))
    if not hits:
        return None
    stem = hits[0][:-len(".transcript2.json")]
    needed = (".scenes", ".voice", ".clip_cuts", ".text_sim")
    if any(not os.path.exists(stem + k + ".json") for k in needed):
        return None
    return stem


def load_inputs(stem):
    def j(kind):
        p = f"{stem}.{kind}.json"
        return json.load(open(p)) if os.path.exists(p) else None
    segs = j("transcript2")["segments"]
    scenes = j("scenes")["scenes"]
    voice = j("voice")
    fades = (j("fades") or {}).get("fades", [])
    clip = {float(k): v for k, v in (j("clip_cuts") or {}).items()}
    tsim = {float(k): v for k, v in (j("text_sim") or {}).items()}
    dur = 0.0
    if segs:
        dur = max(dur, segs[-1]["end"])
    if voice and voice.get("db"):
        dur = max(dur, len(voice["db"]) / voice["rate"])
    return segs, dur, scenes, voice, clip, tsim, fades


def run_one(stem, settings):
    segs, dur, scenes, voice, clip, tsim, fades = load_inputs(stem)
    clip_th = float(settings.get("clip_threshold", SAME_THRESHOLD))
    clip_real = {c for c, s in clip.items() if s is not None and s < clip_th}
    markers = select_ad_breaks_local(segs, dur, settings, scene_cuts=scenes,
                                     voice_env=voice, clip_real_cuts=clip_real,
                                     text_sims=tsim, fade_cuts=fades)
    slots = pick_primary(markers, dur, settings)
    primary = [m for slot in slots for m in slot]
    return markers, primary


def recall(times, gts, tol=TOL):
    return sum(1 for g in gts if any(abs(t - g) <= tol for t in times))


def measure(genre_label, overrides, settings):
    gt_path = GT_TXT.get(genre_label)
    if gt_path:
        gt = load_gt_txt(os.path.join(ROOT, "eval", gt_path))
        resolved = {}
    else:
        gt, resolved = load_gt_json(os.path.join(ROOT, "eval", "ground_truth.json"))

    rows, miss = [], 0
    tot = dict(gt=0, all=0, prim=0, n_all=0)
    for ep, gts in sorted(gt.items()):
        stem = stem_for(ep, resolved)
        if not stem:
            miss += 1
            continue
        markers, primary = run_one(stem, settings)
        a = recall([m["time"] for m in markers], gts)
        p = recall([m["time"] for m in primary], gts)
        rows.append((ep, len(gts), a, p, len(markers)))
        tot["gt"] += len(gts); tot["all"] += a
        tot["prim"] += p; tot["n_all"] += len(markers)
    return rows, tot, miss


def parse_overrides(pairs):
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        k = k.strip()
        v = v.strip()
        if v.lower() in ("true", "false"):
            out[k] = v.lower() == "true"
        else:
            out[k] = float(v)
    return out


def print_block(title, rows, tot):
    print(f"\n=== {title} ===")
    print(f"{'에피소드':<16}{'GT':>4}{'전체재현':>8}{'1차재현':>8}{'마커수':>7}")
    print("-" * 43)
    for ep, n, a, p, na in rows:
        print(f"{ep:<16}{n:>4}{a:>8}{p:>8}{na:>7}")
    print("-" * 43)
    pct = 100 * tot["all"] / tot["gt"] if tot["gt"] else 0
    ppct = 100 * tot["prim"] / tot["gt"] if tot["gt"] else 0
    print(f"{'합계':<16}{tot['gt']:>4}{tot['all']:>8}{tot['prim']:>8}{tot['n_all']:>7}")
    print(f"전체 마커 재현: {tot['all']}/{tot['gt']} ({pct:.0f}%)  |  "
          f"1차 선발 재현: {tot['prim']}/{tot['gt']} ({ppct:.0f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("genre", help="genres.json 의 label/id (예: 자취남)")
    ap.add_argument("--set", dest="sets", action="append", default=[],
                    help="설정 override (예: --set clip_threshold=0.80). 주면 현재값과 A/B 비교")
    args = ap.parse_args()

    genre = find_genre(args.genre)
    overrides = parse_overrides(args.sets)

    base_settings = build_settings(genre, {})
    rows_b, tot_b, miss = measure(args.genre, {}, base_settings)
    if miss:
        print(f"(캐시 없어 건너뛴 에피소드: {miss}편 — .cache 에 feature 캐시가 있어야 측정 가능)")
    print_block(f"현재 genres.json — {args.genre}", rows_b, tot_b)

    if overrides:
        over_settings = build_settings(genre, overrides)
        rows_o, tot_o, _ = measure(args.genre, overrides, over_settings)
        label = ", ".join(f"{k}={v}" for k, v in overrides.items())
        print_block(f"override — {label}", rows_o, tot_o)
        d_all = tot_o["all"] - tot_b["all"]
        d_pr = tot_o["prim"] - tot_b["prim"]
        d_mk = tot_o["n_all"] - tot_b["n_all"]
        print(f"\nΔ 전체 마커 재현 {d_all:+d}  |  Δ 1차 재현 {d_pr:+d}  |  "
              f"Δ 마커수 {d_mk:+d}  (n={len(rows_b)}편)")


if __name__ == "__main__":
    main()
