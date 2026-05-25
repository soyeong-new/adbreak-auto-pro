"""임의 영상 파일들을 분석해서 _adbreaks.xml / _adbreaks_all.xml을 프로젝트 루트에 저장.

사용법:
  cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro

  # 기본: 아래 VIDEO_LIST의 영상 전체 분석
  ../.venv/bin/python eval/analyze_episodes.py

  # 영상 기본 폴더 지정 (VIDEO_LIST 경로 앞부분 덮어쓰기):
  ../.venv/bin/python eval/analyze_episodes.py --base /Volumes/다른드라이브/유병재

  # 특정 영상만:
  ../.venv/bin/python eval/analyze_episodes.py /Volumes/AIPP22/유병재/S23/YBJ_S23_EP01.mp4

  # 시즌 폴더 전체:
  ../.venv/bin/python eval/analyze_episodes.py --season /Volumes/AIPP22/유병재/S23

  # 이미 XML 있는 영상 건너뛰기 (기본 ON):
  ../.venv/bin/python eval/analyze_episodes.py --no-skip

이 스크립트는 Mac 터미널에서 직접 실행해야 합니다.
(mlx-whisper / faster-whisper / PySceneDetect 필요)
"""
import os
import sys
import time
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "eval"))

from analyzer import run_analysis  # noqa: E402


# ── 정답 있는 영상 기본 목록 ──────────────────────────────────────────────────
# ground_truth.txt에 있는 S23/S24/S25 중 드라이브에 실제 존재하는 것만 포함.
# EP07/08/09는 드라이브에 없음.
# --base 인자로 덮어쓸 수 있음. 예: --base /Volumes/다른드라이브/유병재
_YBJ_BASE_DEFAULT = "/Volumes/AIPP22/유병재"
_YBJ_BASE = _YBJ_BASE_DEFAULT  # main()에서 --base 인자로 갱신됨

VIDEO_LIST = [
    # S23 (EP07/08/09 없음)
    f"{_YBJ_BASE}/S23/YBJ_S23_EP01.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP02.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP03.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP04.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP05.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP06.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP10.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP11.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP12.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP13.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP14.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP15.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP16.mp4",
    f"{_YBJ_BASE}/S23/YBJ_S23_EP17.mp4",
    # S24 (EP01 파일명에 언더스코어 주의: YBJ_S24_EP_01.mp4)
    f"{_YBJ_BASE}/S24/YBJ_S24_EP_01.mp4",
    f"{_YBJ_BASE}/S24/YBJ_S24_EP02.mp4",
    f"{_YBJ_BASE}/S24/YBJ_S24_EP03.mp4",
    # S25
    f"{_YBJ_BASE}/S25/YBJ_S25_EP01.mp4",
    f"{_YBJ_BASE}/S25/YBJ_S25_EP02.mp4",
    f"{_YBJ_BASE}/S25/YBJ_S25_EP03.mp4",
]


def xml_exists(video_path: str) -> bool:
    stem = os.path.splitext(os.path.basename(video_path))[0]
    primary = os.path.join(PROJECT_ROOT, f"{stem}_adbreaks.xml")
    return os.path.exists(primary)


def analyze_one(video_path: str):
    stem = os.path.splitext(os.path.basename(video_path))[0]
    primary_out = os.path.join(PROJECT_ROOT, f"{stem}_adbreaks.xml")
    all_out     = os.path.join(PROJECT_ROOT, f"{stem}_adbreaks_all.xml")

    def progress(msg):
        print(f"  · {msg}", flush=True)

    t0 = time.time()
    try:
        report = run_analysis(video_path, progress=progress)
    except Exception as e:
        print(f"  ✗ ERROR: {e}", flush=True)
        return False

    with open(primary_out, "w", encoding="utf-8") as f:
        f.write(report["xml_primary"])
    with open(all_out, "w", encoding="utf-8") as f:
        f.write(report["xml_all"])

    elapsed = time.time() - t0
    n_primary = report.get("primary_count", "?")
    n_markers = report.get("marker_count", "?")
    print(f"  ✓ 완료 — 광고 {n_primary}개 / 전체후보 {n_markers}개 "
          f"({elapsed:.0f}s)", flush=True)
    print(f"    → {primary_out}", flush=True)
    print(f"    → {all_out}", flush=True)
    return True


def collect_from_season(season_dir: str):
    """season_dir 안의 mp4 파일 목록 (._로 시작하는 맥 리소스 포크 제외)."""
    files = []
    for f in sorted(os.listdir(season_dir)):
        if f.endswith(".mp4") and not f.startswith("._"):
            files.append(os.path.join(season_dir, f))
    return files


def main():
    ap = argparse.ArgumentParser(description="영상 분석 → _adbreaks.xml 생성")
    ap.add_argument("videos", nargs="*",
                    help="분석할 mp4 파일 경로들 (생략 시 VIDEO_LIST 사용)")
    ap.add_argument("--base", metavar="DIR",
                    help=f"영상 기본 폴더 (기본값: {_YBJ_BASE_DEFAULT}). VIDEO_LIST 경로 앞부분을 이 경로로 교체합니다.")
    ap.add_argument("--season", metavar="DIR",
                    help="시즌 폴더 경로: 해당 폴더의 모든 mp4 분석")
    ap.add_argument("--no-skip", action="store_true",
                    help="이미 XML 있어도 재분석")
    args = ap.parse_args()

    if args.base:
        global _YBJ_BASE
        _YBJ_BASE = args.base.rstrip("/")
        # VIDEO_LIST를 새 base로 재생성
        global VIDEO_LIST
        VIDEO_LIST = [p.replace(_YBJ_BASE_DEFAULT, _YBJ_BASE) for p in VIDEO_LIST]

    if args.season:
        videos = collect_from_season(args.season)
    elif args.videos:
        videos = args.videos
    else:
        videos = VIDEO_LIST

    print(f"총 {len(videos)}편 분석 예정", flush=True)
    print(f"프로젝트 루트: {PROJECT_ROOT}\n", flush=True)

    ok, skipped, failed = 0, 0, 0
    for i, vpath in enumerate(videos, 1):
        stem = os.path.splitext(os.path.basename(vpath))[0]
        print(f"[{i}/{len(videos)}] {stem}", flush=True)

        if not os.path.exists(vpath):
            print(f"  ✗ 파일 없음: {vpath}", flush=True)
            failed += 1
            continue

        if not args.no_skip and xml_exists(vpath):
            print(f"  → XML 이미 있음, 건너뜀", flush=True)
            skipped += 1
            continue

        if analyze_one(vpath):
            ok += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"완료: {ok}편 | 건너뜀: {skipped}편 | 실패: {failed}편")
    print(f"{'='*60}")

    if ok > 0:
        print("\n다음 단계:")
        print(f"  cd {PROJECT_ROOT}")
        print("  .venv/bin/python eval/load_ground_truth.py ground_truth.txt \\")
        print("    --xml-dir . --out eval/ground_truth.json")
        print("  .venv/bin/python eval/extract_features.py")
        print("  .venv/bin/python eval/train_score.py")


if __name__ == "__main__":
    main()
