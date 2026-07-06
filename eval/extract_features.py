"""기존 마커 XML + 정답 JSON에서 피처 행렬 추출 (extract_features.py)

*_adbreaks_all.xml (없으면 *_adbreaks.xml)의 광고 삽입 후보 마커마다
<comment> 필드를 정규식으로 파싱해 16개 피처 + 레이블 1개로 구성된 행을 생성합니다.
영상 재분석 없이 XML만 읽어 피처를 뽑습니다.

피처 목록:
  score          float  _score()의 종합 점수
  has_cut        bool   "[검증전환]"이 <name>에 포함 → CLIP이 장면 전환 확인
  scene_cut_det  bool   댓글에 "장면 컷에서 시작" 포함 (CLIP 거부 가능성 있음)
  clip_not_cut   bool   "장면 전환 아님" → CLIP이 컷 거부
  clip_passed    bool   "CLIP 검수 통과" → CLIP이 컷 확인
  clip_sim       float  CLIP 유사도 값 (없으면 NaN)
  cut_dist       float  가장 가까운 장면 컷까지의 거리(초) (없으면 NaN)
  silence_db     float  침묵 dB 낙폭 (음수, 예: -30.0; 없으면 NaN)
  long_silence   bool   "긴 침묵" → silence_len >= LONG_SILENCE 임계값
  frame_00       bool   "최우선 :00 프레임"
  strong_opener  bool   "화제 전환 표현으로 시작"
  weak_opener    bool   "전환 표현으로 시작" (화제-전환은 아님)
  closer         bool   "마무리 표현으로 종료"
  short_prev     bool   "너무 짧음(조각)"
  qa             bool   "질문(자문자답"
  continuation   bool   "발화 지속 표현"
  cta            bool   "CTA/홍보 키워드"

레이블:
  label          int    마커 시간이 정답 시간 ±TOL초 이내면 1, 아니면 0

출력:
  eval/output/features.json   — 행 딕셔너리 목록 (사람이 읽기 좋은 형식)
  eval/output/features.csv    — pandas / sklearn 직접 사용 가능한 CSV
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(EVAL_DIR)

DEFAULT_GT = os.path.join(EVAL_DIR, "ground_truth.json")
DEFAULT_XML_DIR = PROJECT_ROOT
DEFAULT_OUT_JSON = os.path.join(EVAL_DIR, "output", "features.json")
DEFAULT_OUT_CSV = os.path.join(EVAL_DIR, "output", "features.csv")

LABEL_TOL = 5.0   # seconds — generous to absorb GT rounding + frame offsets

# ── regex helpers ──────────────────────────────────────────────────────────────

_RE_SCORE       = re.compile(r"점수\s+([-\d.]+)")
_RE_SILENCE_DB  = re.compile(r"음성\s*멈춤\(\s*([-\d.]+)\s*dB\)")
_RE_CLIP_SIM_1  = re.compile(r"장면\s*전환\s*아님[^)]*유사도\s+([\d.]+)")   # rejected
_RE_CLIP_SIM_2  = re.compile(r"CLIP\s*검수\s*통과[^)]*유사도\s+([\d.]+)")   # passed
_RE_CUT_DIST    = re.compile(r"장면\s*컷에서\s*시작\(\s*([\d.]+)\s*s\)")
_RE_TEXT_SIM    = re.compile(r"텍스트유사도\s+([\d.]+)")


def _f(m) -> Optional[float]:
    return float(m.group(1)) if m else None


def _nan() -> float:
    return float("nan")


def parse_comment(name: str, comment: str) -> dict:
    """Return a feature dict from a single marker's <name> and <comment>."""
    has_cut       = "[검증전환]" in name

    scene_cut_det = bool(_RE_CUT_DIST.search(comment))
    clip_not_cut  = "장면 전환 아님" in comment
    clip_passed   = "CLIP 검수 통과" in comment

    # clip_sim: prefer the explicit "통과" value; fall back to "아님" value
    m2 = _RE_CLIP_SIM_2.search(comment)
    m1 = _RE_CLIP_SIM_1.search(comment)
    clip_sim_val  = _f(m2) if m2 else (_f(m1) if m1 else _nan())

    cut_dist_val  = _f(_RE_CUT_DIST.search(comment))
    if cut_dist_val is None:
        cut_dist_val = _nan()

    silence_db_val = _f(_RE_SILENCE_DB.search(comment))
    if silence_db_val is None:
        silence_db_val = _nan()

    score_val     = _f(_RE_SCORE.search(comment))
    if score_val is None:
        score_val = _nan()

    long_silence  = "긴 침묵" in comment
    frame_00      = "최우선 :00 프레임" in comment

    # opener: match in priority order (strong is more specific)
    strong_opener = "화제 전환 표현으로 시작" in comment
    weak_opener   = ("전환 표현으로 시작" in comment) and (not strong_opener)

    closer        = "마무리 표현으로 종료" in comment
    short_prev    = "너무 짧음(조각)" in comment
    qa            = "질문(자문자답" in comment
    continuation  = "발화 지속 표현" in comment
    cta           = "CTA/홍보 키워드" in comment

    text_sim_val  = _f(_RE_TEXT_SIM.search(comment))
    if text_sim_val is None:
        text_sim_val = _nan()
    topic_change  = "주제 전환(" in comment

    return {
        "score":         score_val,
        "has_cut":       has_cut,
        "scene_cut_det": scene_cut_det,
        "clip_not_cut":  clip_not_cut,
        "clip_passed":   clip_passed,
        "clip_sim":      clip_sim_val,
        "cut_dist":      cut_dist_val,
        "silence_db":    silence_db_val,
        "long_silence":  long_silence,
        "frame_00":      frame_00,
        "strong_opener": strong_opener,
        "weak_opener":   weak_opener,
        "closer":        closer,
        "short_prev":    short_prev,
        "qa":            qa,
        "continuation":  continuation,
        "cta":           cta,
        "text_sim":      text_sim_val,
        "topic_change":  topic_change,
    }


def load_xml_markers(xml_path: str) -> List[Tuple[float, str, str]]:
    """Return [(time_sec, name, comment), ...] from a marker XML."""
    tree = ET.parse(xml_path)
    seq  = tree.getroot().find("sequence")
    if seq is None:
        return []
    rows = []
    for mk in seq.findall("marker"):
        in_el   = mk.findtext("in")
        name    = mk.findtext("name") or ""
        comment = mk.findtext("comment") or ""
        if in_el:
            rows.append((int(in_el) / 30.0, name, comment))
    return rows


def find_xml(stem: str, xml_dir: str) -> Optional[str]:
    """Prefer *_adbreaks_all.xml; fall back to *_adbreaks.xml."""
    for suffix in ("_adbreaks_all.xml", "_adbreaks.xml"):
        p = os.path.join(xml_dir, stem + suffix)
        if os.path.exists(p):
            return p
    return None


def label_rows(rows: List[Tuple[float, dict]],
               gt_times: List[float],
               tol: float = LABEL_TOL) -> List[dict]:
    """Assign label=1/0 to each row by matching against GT times.

    Each GT time can match at most one marker (nearest within tol).
    Multiple markers can be near the same GT; only the nearest gets label=1.
    """
    gt_used = set()
    labeled = []
    for time_sec, feat in rows:
        best_gt = None
        best_err = tol + 1.0
        for idx, gt in enumerate(gt_times):
            err = abs(time_sec - gt)
            if err <= tol and err < best_err:
                best_err = err
                best_gt  = idx
        feat = dict(feat)
        feat["time_sec"] = round(time_sec, 2)
        if best_gt is not None and best_gt not in gt_used:
            feat["label"]  = 1
            feat["gt_err"] = round(best_err, 2)
            gt_used.add(best_gt)
        else:
            feat["label"]  = 0
            feat["gt_err"] = None
        labeled.append(feat)
    return labeled


def extract_features(gt_json_path: str = DEFAULT_GT,
                     xml_dir: str = DEFAULT_XML_DIR,
                     tol: float = LABEL_TOL) -> Tuple[List[dict], dict]:
    """Main entry point. Returns (rows, stats)."""
    with open(gt_json_path, encoding="utf-8") as f:
        gt_data = json.load(f)

    gt      = gt_data["ground_truth"]   # {ep_key: [seconds, ...]}
    resolved = gt_data.get("resolved", {})

    all_rows: List[dict] = []
    stats = {
        "episodes_processed":  0,
        "episodes_skipped":    0,
        "skipped_no_xml":      [],
        "skipped_no_gt":       [],
        "total_markers":       0,
        "total_label_1":       0,
        "total_label_0":       0,
    }

    for ep_key, gt_times in sorted(gt.items()):
        stem = resolved.get(ep_key)
        if not stem:
            stats["episodes_skipped"] += 1
            stats["skipped_no_xml"].append(ep_key)
            continue

        xml_path = find_xml(stem, xml_dir)
        if xml_path is None:
            stats["episodes_skipped"] += 1
            stats["skipped_no_xml"].append(ep_key)
            continue

        raw_markers = load_xml_markers(xml_path)
        if not raw_markers:
            stats["episodes_skipped"] += 1
            continue

        rows_with_feat = [
            (t, parse_comment(name, comment))
            for t, name, comment in raw_markers
        ]
        labeled = label_rows(rows_with_feat, gt_times, tol)
        for row in labeled:
            row["ep"]      = ep_key
            row["xml_file"] = os.path.basename(xml_path)

        all_rows.extend(labeled)
        n1 = sum(r["label"] for r in labeled)
        stats["episodes_processed"] += 1
        stats["total_markers"]      += len(labeled)
        stats["total_label_1"]      += n1
        stats["total_label_0"]      += len(labeled) - n1

    return all_rows, stats


# ── CSV helpers ────────────────────────────────────────────────────────────────

FLOAT_COLS  = ["score", "clip_sim", "cut_dist", "silence_db", "text_sim", "time_sec", "gt_err"]
BOOL_COLS   = ["has_cut", "scene_cut_det", "clip_not_cut", "clip_passed",
               "long_silence", "frame_00", "strong_opener", "weak_opener",
               "closer", "short_prev", "qa", "continuation", "cta", "topic_change"]
INT_COLS    = ["label"]
STR_COLS    = ["ep", "xml_file"]
CSV_COLS    = STR_COLS + ["time_sec"] + FLOAT_COLS[:-2] + BOOL_COLS + INT_COLS + ["gt_err"]


def _fmt(val) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and math.isnan(val):
        return ""
    if isinstance(val, bool):
        return "1" if val else "0"
    return str(val)


def rows_to_csv(rows: List[dict], path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt(row.get(k)) for k in CSV_COLS})


def rows_to_json(rows: List[dict], path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def _clean(v):
        if isinstance(v, float) and math.isnan(v):
            return None
        return v

    cleaned = [{k: _clean(v) for k, v in row.items()} for row in rows]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=1)


# ── CLI ────────────────────────────────────────────────────────────────────────

def _cli():
    p = argparse.ArgumentParser(
        description="Extract feature matrix from marker XMLs + ground-truth JSON.")
    p.add_argument("--gt",      default=DEFAULT_GT)
    p.add_argument("--xml-dir", default=DEFAULT_XML_DIR)
    p.add_argument("--tol",     type=float, default=LABEL_TOL,
                   help=f"GT matching tolerance in seconds (default {LABEL_TOL})")
    p.add_argument("--out-json", default=DEFAULT_OUT_JSON)
    p.add_argument("--out-csv",  default=DEFAULT_OUT_CSV)
    args = p.parse_args()

    rows, stats = extract_features(args.gt, args.xml_dir, args.tol)

    rows_to_json(rows, args.out_json)
    rows_to_csv(rows,  args.out_csv)

    print(f"✓ {stats['episodes_processed']}편 처리, "
          f"{stats['total_markers']}개 마커 "
          f"(label=1: {stats['total_label_1']}, label=0: {stats['total_label_0']})")

    if stats["skipped_no_xml"]:
        print(f"⚠ {len(stats['skipped_no_xml'])}편 XML 없어서 건너뜀: "
              f"{stats['skipped_no_xml'][:6]}{'...' if len(stats['skipped_no_xml'])>6 else ''}")

    print(f"\nCSV:  {args.out_csv}")
    print(f"JSON: {args.out_json}")

    # 간단 분포 요약
    if rows:
        import statistics
        scores_1 = [r["score"] for r in rows if r["label"] == 1
                    and r["score"] is not None and not math.isnan(r["score"])]
        scores_0 = [r["score"] for r in rows if r["label"] == 0
                    and r["score"] is not None and not math.isnan(r["score"])]
        if scores_1 and scores_0:
            print(f"\n점수 분포 (GT hit / miss):")
            print(f"  label=1 ({len(scores_1)}개): "
                  f"mean={statistics.mean(scores_1):.2f}, "
                  f"median={statistics.median(scores_1):.2f}")
            print(f"  label=0 ({len(scores_0)}개): "
                  f"mean={statistics.mean(scores_0):.2f}, "
                  f"median={statistics.median(scores_0):.2f}")

        feat_cols = BOOL_COLS
        print(f"\nfeature prevalence (label=1 vs label=0):")
        print(f"  {'feature':20s}  {'hit %':>7}  {'miss %':>7}")
        rows_1 = [r for r in rows if r["label"] == 1]
        rows_0 = [r for r in rows if r["label"] == 0]
        for fc in feat_cols:
            if rows_1:
                p1 = 100 * sum(1 for r in rows_1 if r.get(fc)) / len(rows_1)
            else:
                p1 = 0.0
            if rows_0:
                p0 = 100 * sum(1 for r in rows_0 if r.get(fc)) / len(rows_0)
            else:
                p0 = 0.0
            if abs(p1 - p0) > 3:   # only print discriminative features
                print(f"  {fc:20s}  {p1:>6.1f}%  {p0:>6.1f}%")


if __name__ == "__main__":
    _cli()
