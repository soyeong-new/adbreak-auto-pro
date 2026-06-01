"""Path 1(침묵)에 주제전환 가점(w_topic_change)을 추가할 때의 영향 측정.

현재: Path 1 마커는 텍스트 유사도를 점수에 반영 안 함.
실험: Path 1 마커 시각에서 텍스트 유사도를 계산해 < 0.75면 +w_topic 가산 후,
1차 선발/전체 재현이 어떻게 바뀌는지 YBJ/SKA(정답 보유) 캐시로 비교.

영상 없이 캐시만 사용. text_similarity 모델만 추가로 로드.
"""
import os, sys, glob, json, copy
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import local_breaks as lb
from scene_verify import SAME_THRESHOLD
from text_similarity import compute_text_similarity

CACHE = os.path.join(ROOT, ".cache")
TOL = 5.0
W_TOPIC = 4.0          # 기본/토크 값
TEXT_TH = 0.75


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
    real = {c for c, s in clip.items() if s is not None and s < SAME_THRESHOLD}
    dur = 0.0
    if segs:
        dur = max(dur, segs[-1]["end"])
    if voice and voice.get("db"):
        dur = max(dur, len(voice["db"]) / voice["rate"])
    return segs, dur, scenes, voice, real, tsim, fades


def recall(times, gts, tol=TOL):
    return sum(1 for g in gts if any(abs(t - g) <= tol for t in times))


def add_path1_topic(markers, segments):
    """Path 1(침묵) 마커에 주제전환 가점을 적용한 사본 반환."""
    out = copy.deepcopy(markers)
    applied = 0
    for m in out:
        # Path 2/3는 cut_anchor/fade_anchor 플래그를 가짐 → Path 1만 대상
        if m.get("cut_anchor") or m.get("fade_anchor"):
            continue
        sim = compute_text_similarity(segments, m["time"])
        if sim is not None and sim < TEXT_TH:
            m["score"] = round(m["score"] + W_TOPIC, 2)
            applied += 1
    return out, applied


def main():
    gt = json.load(open(os.path.join(ROOT, "eval/ground_truth.json")))
    gmark, resolved = gt["ground_truth"], gt["resolved"]

    rows = []
    T = dict(gt=0, a_o=0, a_n=0, p_o=0, p_n=0, applied=0, changed=0)
    for ep, fn in resolved.items():
        if not fn:
            continue
        hits = glob.glob(f"{CACHE}/{fn}*.transcript2.json")
        if not hits:
            continue
        stem = hits[0][:-len(".transcript2.json")]
        if any(not os.path.exists(stem + k + ".json")
               for k in (".scenes", ".voice", ".clip_cuts", ".text_sim")):
            continue
        gts = gmark.get(ep, [])
        if not gts:
            continue
        segs, dur, scenes, voice, real, tsim, fades = load_inputs(stem)
        base = lb.select_ad_breaks_local(segs, dur, None, scene_cuts=scenes,
                                         voice_env=voice, clip_real_cuts=real,
                                         text_sims=tsim, fade_cuts=fades)
        new, applied = add_path1_topic(base, segs)

        pr_o = [m for slot in lb.pick_primary(base, dur, None) for m in slot]
        pr_n = [m for slot in lb.pick_primary(new, dur, None) for m in slot]

        a_o = recall([m["time"] for m in base], gts)
        a_n = recall([m["time"] for m in new], gts)
        p_o = recall([m["time"] for m in pr_o], gts)
        p_n = recall([m["time"] for m in pr_n], gts)
        ch = sorted(round(m["time"], 2) for m in pr_o) != \
            sorted(round(m["time"], 2) for m in pr_n)

        rows.append((ep, len(gts), p_o, p_n, applied, ch))
        T["gt"] += len(gts); T["a_o"] += a_o; T["a_n"] += a_n
        T["p_o"] += p_o; T["p_n"] += p_n; T["applied"] += applied; T["changed"] += ch
        print(f"  {ep:<11} 1차 {p_o}→{p_n}  주제가점 {applied}개"
              f"{'  [1차변화]' if ch else ''}", flush=True)

    print("-" * 52)
    print(f"전체 마커 재현: {T['a_o']} → {T['a_n']} ({T['a_n']-T['a_o']:+d})")
    print(f"1차 선발 재현: {T['p_o']} → {T['p_n']} ({T['p_n']-T['p_o']:+d})  / 정답 {T['gt']}개")
    print(f"주제전환 가점 적용된 Path1 마커: 총 {T['applied']}개")
    print(f"1차 선발 바뀐 에피소드: {T['changed']}/{len(rows)}편")


if __name__ == "__main__":
    main()
