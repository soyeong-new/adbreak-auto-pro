"""자취남(JCN)에서 Path 1 주제전환 가점의 효과 측정.

새로 분석한 JCN 캐시 + jcn_ground_truth.txt 로,
Path1(침묵) 마커에 주제전환 가점(w_topic=7)을 적용했을 때
정답 재현(전체/1차)이 어떻게 바뀌는지 비교.

실행: ../.venv/bin/python eval/measure_jcn_topic.py
"""
import os, sys, glob, json, copy
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

import local_breaks as lb
from scene_verify import SAME_THRESHOLD
from text_similarity import compute_text_similarity
from load_ground_truth import load_from_file

CACHE = os.path.join(ROOT, ".cache")
TOL = 5.0
W_TOPIC = 7.0          # 자취남 값
TEXT_TH = 0.75


def find_stem(ep_filename):
    """ep_filename(예: JCN_S04_EP02_HD_KR) → 캐시 stem."""
    hits = glob.glob(f"{CACHE}/{ep_filename}_*.transcript2.json")
    if not hits:
        return None
    return hits[0][:-len(".transcript2.json")]


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
    out = copy.deepcopy(markers)
    applied = 0
    for m in out:
        if m.get("cut_anchor") or m.get("fade_anchor"):
            continue
        sim = compute_text_similarity(segments, m["time"])
        if sim is not None and sim < TEXT_TH:
            m["score"] = round(m["score"] + W_TOPIC, 2)
            applied += 1
    return out, applied


# JCN ground_truth.txt 의 ep 키(예: s4_ep2) → 파일명(JCN_S04_EP02_HD_KR)
def ep_to_filename(ep_key):
    import re
    m = re.match(r"s(\d+)_ep(\d+)", ep_key)
    if not m:
        return None
    s, e = int(m.group(1)), int(m.group(2))
    return f"JCN_S{s:02d}_EP{e:02d}_HD_KR"


def main():
    gt, _ = load_from_file(os.path.join(ROOT, "eval/jcn_ground_truth.txt"))

    rows = []
    T = dict(gt=0, a_o=0, a_n=0, p_o=0, p_n=0, applied=0, changed=0)
    for ep_key, gts in sorted(gt.items()):
        if not gts:
            continue
        fn = ep_to_filename(ep_key)
        if not fn:
            continue
        stem = find_stem(fn)
        if stem is None:
            continue
        if any(not os.path.exists(stem + k + ".json")
               for k in (".scenes", ".voice", ".clip_cuts")):
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
        rows.append((fn, len(gts), a_o, a_n, p_o, p_n, applied, ch))
        T["gt"] += len(gts); T["a_o"] += a_o; T["a_n"] += a_n
        T["p_o"] += p_o; T["p_n"] += p_n; T["applied"] += applied; T["changed"] += ch
        print(f"  {fn:<22} GT{len(gts)} 전체{a_o}→{a_n} 1차{p_o}→{p_n} "
              f"가점{applied}{'  [1차변화]' if ch else ''}", flush=True)

    print("-" * 60)
    print(f"측정 에피소드: {len(rows)}편 / 정답 {T['gt']}개")
    print(f"전체 마커 재현: {T['a_o']} → {T['a_n']} ({T['a_n']-T['a_o']:+d})")
    print(f"1차 선발 재현: {T['p_o']} → {T['p_n']} ({T['p_n']-T['p_o']:+d})")
    print(f"주제가점 적용된 Path1 마커: {T['applied']}개")
    print(f"1차 선발 바뀐 에피소드: {T['changed']}/{len(rows)}편")


if __name__ == "__main__":
    main()
