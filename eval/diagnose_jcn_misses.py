"""자취남에서 후보로 안 잡힌 정답 지점 근처에 어떤 신호가 있는지 진단.

각 정답 ±5s 안에: 문장경계 / 침묵 / CLIP컷 / 페이드 / 허용프레임 존재 여부.
어느 Path를 손봐야 자취남 정답을 더 잡을지 알아내기 위함.
"""
import os, sys, glob, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

import local_breaks as lb
from scene_verify import SAME_THRESHOLD
from topic_breaks import build_sentences
from framecode import FPS, FF_ALLOWED
from load_ground_truth import load_from_file

CACHE = os.path.join(ROOT, ".cache")
TOL = 5.0


def find_stem(fn):
    h = glob.glob(f"{CACHE}/{fn}_*.transcript2.json")
    return h[0][:-len(".transcript2.json")] if h else None


def ep_to_filename(ep_key):
    import re
    m = re.match(r"s(\d+)_ep(\d+)", ep_key)
    return f"JCN_S{int(m.group(1)):02d}_EP{int(m.group(2)):02d}_HD_KR" if m else None


def main():
    gt, _ = load_from_file(os.path.join(ROOT, "eval/jcn_ground_truth.txt"))
    stats = dict(total=0, hit=0, miss=0,
                 miss_sent=0, miss_sil=0, miss_cut=0, miss_fade=0, miss_frame=0)
    print(f"{'에피소드':<22}{'정답':>7} {'후보?':>5} {'문장':>4}{'침묵':>4}{'컷':>4}{'페이드':>5}{'프레임':>5}")
    print("-" * 64)
    for ep_key, gts in sorted(gt.items()):
        fn = ep_to_filename(ep_key)
        stem = find_stem(fn) if fn else None
        if not stem or not os.path.exists(stem + ".scenes.json"):
            continue

        def j(k):
            p = f"{stem}.{k}.json"
            return json.load(open(p)) if os.path.exists(p) else None
        segs = j("transcript2")["segments"]
        scenes = j("scenes")["scenes"]
        voice = j("voice")
        fades = (j("fades") or {}).get("fades", [])
        clip = {float(k): v for k, v in (j("clip_cuts") or {}).items()}
        real = {c for c, s in clip.items() if s is not None and s < SAME_THRESHOLD}
        dur = max(segs[-1]["end"], len(voice["db"]) / voice["rate"])
        sents = build_sentences(segs)
        nf = lb._noise_floor(voice) if voice.get("db") else -70.0

        markers = lb.select_ad_breaks_local(segs, dur, None, scene_cuts=scenes,
                                            voice_env=voice, clip_real_cuts=real,
                                            text_sims={}, fade_cuts=fades)
        mtimes = [m["time"] for m in markers]

        for g in gts:
            stats["total"] += 1
            is_hit = any(abs(t - g) <= TOL for t in mtimes)
            # 신호 존재 여부 (±5s)
            has_sent = any(abs(s["end"] - g) <= TOL or abs(s["start"] - g) <= TOL
                           for s in sents)
            sil = lb._find_silence(voice, g - TOL, g + TOL, nf, min_dur=lb.SILENCE_MIN)
            has_sil = sil is not None
            has_cut = any(abs(c - g) <= TOL for c in scenes)
            has_realcut = any(abs(c - g) <= TOL for c in real)
            has_fade = any(abs(f - g) <= TOL for f in fades)
            # 허용 프레임이 ±5s 안에 있는지 (항상 있음 — 1초에 6프레임꼴)
            has_frame = True

            if is_hit:
                stats["hit"] += 1
            else:
                stats["miss"] += 1
                if not has_sent: stats["miss_sent"] += 1
                if not has_sil: stats["miss_sil"] += 1
                if not has_realcut: stats["miss_cut"] += 1
                if not has_fade: stats["miss_fade"] += 1
            mark = "○" if is_hit else "✗"
            print(f"{fn:<22}{int(g//60):>4}:{int(g%60):02d} {mark:>5} "
                  f"{'O' if has_sent else '·':>4}{'O' if has_sil else '·':>4}"
                  f"{'O' if has_realcut else '·':>4}{'O' if has_fade else '·':>5}"
                  f"{'O':>5}")

    print("-" * 64)
    print(f"정답 {stats['total']}개 — 후보로 잡음 {stats['hit']} / 놓침 {stats['miss']}")
    if stats["miss"]:
        m = stats["miss"]
        print(f"놓친 {m}개 중 근처에 신호가 *없던* 비율:")
        print(f"  문장경계 없음: {stats['miss_sent']}/{m}")
        print(f"  침묵 없음:     {stats['miss_sil']}/{m}")
        print(f"  CLIP컷 없음:   {stats['miss_cut']}/{m}")
        print(f"  페이드 없음:   {stats['miss_fade']}/{m}")


if __name__ == "__main__":
    main()
