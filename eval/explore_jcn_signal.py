"""자취남 정답 지점을 일반 문장경계와 구분하는 변별 신호 탐색.

각 문장경계에서 특징을 계산하고, 정답(±5s 안) 경계 vs 나머지 경계의
분포를 비교한다. 어떤 특징이 정답을 분리하는지 확인.

특징:
  sim30  : ±30s 윈도 텍스트 유사도 (현행)
  sim90  : ±90s 윈도 (챕터 수준)
  locmin : ±90s 안에서 sim30이 국소 최저인가 (가장 강한 주제 전환)
  sillen : 경계 침묵 길이(초)
  prevlen: 직전 문장 글자 수
  gap    : 문장 간 시간 간격(초)

효율: 문장별 임베딩을 1회 계산해 재사용.
"""
import os, sys, glob, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "eval"))

import local_breaks as lb
from topic_breaks import build_sentences
from text_similarity import _load, _gather_text
from load_ground_truth import load_from_file
import re

CACHE = os.path.join(ROOT, ".cache")
TOL = 5.0


def fn_of(k):
    m = re.match(r"s(\d+)_ep(\d+)", k)
    return f"JCN_S{int(m.group(1)):02d}_EP{int(m.group(2)):02d}_HD_KR" if m else None


def windowed_sims(model, segs, bts, window):
    """각 경계 시각 bt에서 ±window 텍스트 유사도. 배치 인코딩."""
    import torch
    texts = []
    for bt in bts:
        before = _gather_text(segs, bt, window, "before")
        after = _gather_text(segs, bt, window, "after")
        texts.append((before, after))
    flat = [t for pair in texts for t in pair]
    embs = model.encode(flat, convert_to_tensor=True, normalize_embeddings=True,
                        batch_size=64, show_progress_bar=False)
    sims = []
    for i in range(len(texts)):
        b, a = texts[i]
        if not b.strip() or not a.strip():
            sims.append(None)
        else:
            sims.append(float(torch.dot(embs[2 * i], embs[2 * i + 1]).item()))
    return sims


def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    i = int(p / 100 * (len(vals) - 1))
    return vals[i]


def main():
    gt, _ = load_from_file(os.path.join(ROOT, "eval/jcn_ground_truth.txt"))
    model = _load()

    gt_feats = []      # 정답 경계 특징
    bg_feats = []      # 나머지 경계 특징
    for k, gts in sorted(gt.items()):
        fnm = fn_of(k)
        if not fnm:
            continue
        h = glob.glob(f"{CACHE}/{fnm}_*.transcript2.json")
        if not h:
            continue
        stem = h[0][:-len(".transcript2.json")]
        segs = json.load(open(stem + ".transcript2.json"))["segments"]
        voice = json.load(open(stem + ".voice.json"))
        dur = max(segs[-1]["end"], len(voice["db"]) / voice["rate"])
        nf = lb._noise_floor(voice) if voice.get("db") else -70.0
        sents = build_sentences(segs)
        bts = [sents[i]["end"] for i in range(len(sents) - 1)]
        if not bts:
            continue
        sim30 = windowed_sims(model, segs, bts, 30.0)
        sim90 = windowed_sims(model, segs, bts, 90.0)

        for i, bt in enumerate(bts):
            # 데드존 밖만
            if not (180 <= bt <= dur - 180):
                continue
            # 국소 최저: ±90s 안 다른 경계보다 sim30이 낮은가
            neigh = [sim30[j] for j in range(len(bts))
                     if abs(bts[j] - bt) <= 90 and sim30[j] is not None]
            locmin = (sim30[i] is not None and neigh and sim30[i] == min(neigh))
            sil = lb._find_silence(voice, bt - TOL, bt + TOL, nf, min_dur=0.3)
            sillen = (sil[1] - sil[0]) if sil else 0.0
            prevlen = len(sents[i]["text"])
            gap = sents[i + 1]["start"] - sents[i]["end"]
            feat = dict(sim30=sim30[i], sim90=sim90[i], locmin=locmin,
                        silen=sillen, prevlen=prevlen, gap=gap)
            is_gt = any(abs(bt - g) <= TOL for g in gts)
            (gt_feats if is_gt else bg_feats).append(feat)

    print(f"정답 경계 {len(gt_feats)}개 vs 일반 경계 {len(bg_feats)}개\n")

    def col(feats, key):
        return [f[key] for f in feats]

    print(f"{'특징':<10}{'정답중앙':>10}{'정답10%':>9}{'일반중앙':>10}{'일반10%':>9}")
    print("-" * 50)
    for key in ("sim30", "sim90", "silen", "prevlen", "gap"):
        print(f"{key:<10}{pct(col(gt_feats,key),50) or 0:>10.2f}"
              f"{pct(col(gt_feats,key),10) or 0:>9.2f}"
              f"{pct(col(bg_feats,key),50) or 0:>10.2f}"
              f"{pct(col(bg_feats,key),10) or 0:>9.2f}")
    # locmin 비율
    gt_lm = sum(1 for f in gt_feats if f["locmin"]) / max(1, len(gt_feats))
    bg_lm = sum(1 for f in bg_feats if f["locmin"]) / max(1, len(bg_feats))
    print(f"{'locmin%':<10}{gt_lm*100:>10.1f}{'':>9}{bg_lm*100:>10.1f}")
    print()
    # 침묵 동반 비율
    gt_sil = sum(1 for f in gt_feats if f["silen"] > 0) / max(1, len(gt_feats))
    bg_sil = sum(1 for f in bg_feats if f["silen"] > 0) / max(1, len(bg_feats))
    print(f"침묵 동반: 정답 {gt_sil*100:.0f}% / 일반 {bg_sil*100:.0f}%")


if __name__ == "__main__":
    main()
