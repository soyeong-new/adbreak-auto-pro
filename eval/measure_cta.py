"""CTA 패널티 삭제가 YBJ/SKA(정답 보유 채널) 결과에 주는 영향 측정.

영상 없이 캐시만으로 select_ad_breaks_local + pick_primary 를 돌려
CTA on(p_cta=3) vs off(p_cta=0)의 정답 매칭(±5s)을 비교한다.
_verify(CLIP 재검증)는 영상이 필요해 생략 — 두 조건에 동일하게 빠지므로
'CTA 삭제의 차이'는 그대로 측정된다.
"""
import os, sys, glob, json
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from local_breaks import select_ad_breaks_local, pick_primary
from scene_verify import SAME_THRESHOLD

CACHE = os.path.join(PROJECT_ROOT, ".cache")
TOL = 5.0


def load_inputs(stem):
    def j(kind):
        p = f"{stem}.{kind}.json"
        return json.load(open(p)) if os.path.exists(p) else None
    segs = j("transcript2")["segments"]
    scenes = j("scenes")["scenes"]
    voice = j("voice")
    fades = (j("fades") or {}).get("fades", [])
    clip = j("clip_cuts") or {}
    tsim = j("text_sim") or {}
    clip = {float(k): v for k, v in clip.items()}
    tsim = {float(k): v for k, v in tsim.items()}
    clip_real = {c for c, s in clip.items() if s is not None and s < SAME_THRESHOLD}
    # duration: 마지막 발화 끝 또는 음량 길이 중 큰 값
    dur = 0.0
    if segs:
        dur = max(dur, segs[-1]["end"])
    if voice and voice.get("db"):
        dur = max(dur, len(voice["db"]) / voice["rate"])
    return segs, dur, scenes, voice, clip_real, tsim, fades


def primary_times(segs, dur, scenes, voice, clip_real, tsim, fades, p_cta):
    settings = {"p_cta": p_cta}
    markers = select_ad_breaks_local(segs, dur, settings,
                                     scene_cuts=scenes, voice_env=voice,
                                     clip_real_cuts=clip_real, text_sims=tsim,
                                     fade_cuts=fades)
    slots = pick_primary(markers, dur, settings)
    prim = [m for slot in slots for m in slot]
    cta_fires = sum(1 for m in markers if "CTA" in m["reason"])
    cta_in_prim = sum(1 for m in prim if "CTA" in m["reason"])
    return prim, markers, cta_fires, cta_in_prim


def gt_hits(times, gts, tol=TOL):
    """정답 각각에 대해 ±tol 안에 마커가 있으면 hit. (정답 기준 재현율)"""
    hit = 0
    for g in gts:
        if any(abs(t - g) <= tol for t in times):
            hit += 1
    return hit


def main():
    gt = json.load(open(os.path.join(PROJECT_ROOT, "eval/ground_truth.json")))
    gmark, resolved = gt["ground_truth"], gt["resolved"]

    rows = []
    tot_gt = tot_hit_on = tot_hit_off = 0
    tot_cta = tot_cta_prim = 0
    prim_changed = 0
    for ep, fn in resolved.items():
        if not fn:
            continue
        hits = glob.glob(f"{CACHE}/{fn}*.transcript2.json")
        if not hits:
            continue
        stem = hits[0][:-len(".transcript2.json")]
        for k in (".scenes", ".voice", ".clip_cuts", ".text_sim"):
            if not os.path.exists(stem + k + ".json"):
                stem = None
                break
        if stem is None:
            continue
        gts = gmark.get(ep, [])
        if not gts:
            continue
        data = load_inputs(stem)

        prim_on, mk_on, cta, cta_p = primary_times(*data, p_cta=3.0)
        prim_off, mk_off, _, _ = primary_times(*data, p_cta=0.0)

        # 전체 후보 마커 기준 재현(편집자가 검토하는 모든 후보)
        h_on = gt_hits([m["time"] for m in mk_on], gts)
        h_off = gt_hits([m["time"] for m in mk_off], gts)
        # 1차 선발 시간이 바뀌었는지
        t_on = sorted(round(m["time"], 2) for m in prim_on)
        t_off = sorted(round(m["time"], 2) for m in prim_off)
        changed = t_on != t_off

        rows.append((ep, len(gts), h_on, h_off, cta, cta_p, changed))
        tot_gt += len(gts); tot_hit_on += h_on; tot_hit_off += h_off
        tot_cta += cta; tot_cta_prim += cta_p
        if changed:
            prim_changed += 1

    print(f"{'에피소드':<12}{'GT':>4}{'hit(CTA有)':>10}{'hit(CTA無)':>10}"
          f"{'CTA발동':>8}{'1차CTA':>7}{'선발변화':>8}")
    print("-" * 62)
    for ep, ng, hon, hoff, cta, ctap, ch in rows:
        print(f"{ep:<12}{ng:>4}{hon:>10}{hoff:>10}{cta:>8}{ctap:>7}"
              f"{'  바뀜' if ch else '  동일':>8}")
    print("-" * 62)
    print(f"{'합계':<12}{tot_gt:>4}{tot_hit_on:>10}{tot_hit_off:>10}"
          f"{tot_cta:>8}{tot_cta_prim:>7}")
    print()
    print(f"정답 {tot_gt}개 중 — CTA有 재현 {tot_hit_on} / CTA無 재현 {tot_hit_off}"
          f"  (차이 {tot_hit_off - tot_hit_on:+d})")
    print(f"전체 마커 CTA 발동: {tot_cta}회 / 그중 1차 선발에 든 것: {tot_cta_prim}개")
    print(f"1차 선발 결과가 바뀐 에피소드: {prim_changed}/{len(rows)}편")


if __name__ == "__main__":
    main()
