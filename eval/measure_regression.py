"""구버전 vs 신버전 회귀 비교 — YBJ/SKA(정답 보유) 캐시로.

기본 설정(장르 override 없음)으로 두 버전을 돌려 정답 재현/1차 선발을 비교.
이 조건이 YBJ/SKA가 실제로 테스트된 조건.
"""
import os, sys, glob, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import local_breaks as new
import _local_breaks_old as old
from scene_verify import SAME_THRESHOLD

CACHE = os.path.join(ROOT, ".cache")
TOL = 5.0


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
    clip_real = {c for c, s in clip.items() if s is not None and s < SAME_THRESHOLD}
    dur = 0.0
    if segs:
        dur = max(dur, segs[-1]["end"])
    if voice and voice.get("db"):
        dur = max(dur, len(voice["db"]) / voice["rate"])
    return segs, dur, scenes, voice, clip_real, tsim, fades


def run(mod, data):
    segs, dur, scenes, voice, clip_real, tsim, fades = data
    markers = mod.select_ad_breaks_local(segs, dur, None,
                                         scene_cuts=scenes, voice_env=voice,
                                         clip_real_cuts=clip_real, text_sims=tsim,
                                         fade_cuts=fades)
    slots = mod.pick_primary(markers, dur, None)
    prim = [m for slot in slots for m in slot]
    return markers, prim


def recall(times, gts, tol=TOL):
    return sum(1 for g in gts if any(abs(t - g) <= tol for t in times))


def main():
    gt = json.load(open(os.path.join(ROOT, "eval/ground_truth.json")))
    gmark, resolved = gt["ground_truth"], gt["resolved"]

    rows = []
    T = dict(gt_n=0, all_o=0, all_n=0, pr_o=0, pr_n=0, changed=0)
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
        data = load_inputs(stem)
        mk_o, pr_o = run(old, data)
        mk_n, pr_n = run(new, data)

        a_o = recall([m["time"] for m in mk_o], gts)
        a_n = recall([m["time"] for m in mk_n], gts)
        p_o = recall([m["time"] for m in pr_o], gts)
        p_n = recall([m["time"] for m in pr_n], gts)
        ch = sorted(round(m["time"], 2) for m in pr_o) != \
            sorted(round(m["time"], 2) for m in pr_n)

        rows.append((ep, len(gts), a_o, a_n, p_o, p_n, ch))
        T["gt_n"] += len(gts); T["all_o"] += a_o; T["all_n"] += a_n
        T["pr_o"] += p_o; T["pr_n"] += p_n; T["changed"] += ch

    print(f"{'에피소드':<11}{'GT':>3}{'전체old':>7}{'전체new':>7}"
          f"{'1차old':>7}{'1차new':>7}{'1차변화':>8}")
    print("-" * 52)
    for ep, n, ao, an, po, pn, ch in rows:
        flag = "  바뀜" if ch else ""
        print(f"{ep:<11}{n:>3}{ao:>7}{an:>7}{po:>7}{pn:>7}{flag:>8}")
    print("-" * 52)
    print(f"{'합계':<11}{T['gt_n']:>3}{T['all_o']:>7}{T['all_n']:>7}"
          f"{T['pr_o']:>7}{T['pr_n']:>7}")
    print()
    print(f"전체 마커 재현: old {T['all_o']} → new {T['all_n']} "
          f"({T['all_n']-T['all_o']:+d})")
    print(f"1차 선발 재현: old {T['pr_o']} → new {T['pr_n']} "
          f"({T['pr_n']-T['pr_o']:+d})")
    print(f"1차 선발이 바뀐 에피소드: {T['changed']}/{len(rows)}편")


if __name__ == "__main__":
    main()
