"""구글 드라이브 폴더 감시 → 새 영상 자동 분석 → XML 저장

사용법:
  cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
  ../.venv/bin/python watcher.py "/Users/choisoyeong/Library/CloudStorage/GoogleDrive-so-yeong@its-newid.com/내 드라이브/AD Break"

  # 폴링 간격 30초로 변경:
  ../.venv/bin/python watcher.py "/path/to/folder" --interval 30

  # 시작 시 기존 미처리 파일 스캔 건너뜀:
  ../.venv/bin/python watcher.py "/path/to/folder" --no-scan

하위 폴더(예: 유병재/, 로카/)를 자동으로 감시합니다.
각 폴더에 settings.json을 두면 해당 폴더의 영상에만 그 설정이 적용됩니다.

settings.json 형식:
  {
    "first_min": 3,
    "first_max": 10,
    "gap_min": 10,
    "gap_max": 15
  }
"""
import os
import sys
import time
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from analyzer import run_analysis  # noqa: E402

# 기본 설정값
DEFAULT_SETTINGS = {
    "first_min": 3,
    "first_max": 10,
    "gap_min": 10,
    "gap_max": 15,
}


def load_settings(folder):
    """폴더의 settings.json을 읽어 반환. 없으면 기본값 사용.
    settings.json은 분(minute) 단위로 입력하며, run_analysis에 넘기기 전 초로 변환합니다.
    """
    TIME_KEYS = {"first_min", "first_max", "gap_min", "gap_max",
                 "intro_deadzone", "outro_deadzone"}
    path = os.path.join(folder, "settings.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            merged = {**DEFAULT_SETTINGS, **data}
            print(f"  ⚙ settings.json 적용: {data}", flush=True)
        except Exception as e:
            print(f"  ⚠ settings.json 읽기 실패 ({e}), 기본값 사용", flush=True)
            merged = DEFAULT_SETTINGS.copy()
    else:
        merged = DEFAULT_SETTINGS.copy()

    # 분 → 초 변환
    return {k: (v * 60 if k in TIME_KEYS else v) for k, v in merged.items()}


def wait_for_stable(path, check_interval=15, timeout=86400):
    """ffprobe로 파일이 실제로 읽힐 때까지 대기.

    파일 크기 안정화 방식은 구글 드라이브 대용량 파일에서 오작동(크기가 다운로드
    내내 변함)하므로, ffprobe로 실제 재생 시간 확인에 성공하면 다운로드 완료로 판단.
    timeout 기본값 86400초(24시간) — 사실상 무제한 대기.
    """
    import subprocess
    elapsed = 0
    last_size = -1
    while elapsed < timeout:
        try:
            size = os.path.getsize(path)
        except OSError:
            time.sleep(check_interval)
            elapsed += check_interval
            continue

        if size == 0:
            time.sleep(check_interval)
            elapsed += check_interval
            continue

        if size != last_size:
            # 크기가 변하는 중 — 아직 다운로드 중
            last_size = size
            time.sleep(check_interval)
            elapsed += check_interval
            continue

        # 크기가 멈췄을 때 ffprobe로 실제 가독성 확인
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        time.sleep(check_interval)
        elapsed += check_interval
    return False


def xml_exists(video_path):
    stem = os.path.splitext(os.path.basename(video_path))[0]
    folder = os.path.dirname(video_path)
    return os.path.exists(os.path.join(folder, f"{stem}_adbreaks.xml"))


def process_video(video_path):
    stem = os.path.splitext(os.path.basename(video_path))[0]
    folder = os.path.dirname(video_path)
    primary_out = os.path.join(folder, f"{stem}_adbreaks.xml")
    all_out = os.path.join(folder, f"{stem}_adbreaks_all.xml")

    print(f"\n[분석 시작] {stem}", flush=True)
    print("  파일 안정화 대기 중 (다운로드 완료 확인)...", flush=True)

    if not wait_for_stable(video_path):
        print(f"  ✗ 타임아웃: 파일이 너무 오래 걸립니다 — {stem}", flush=True)
        return

    settings = load_settings(folder)

    try:
        report = run_analysis(
            video_path,
            settings=settings,
            progress=lambda msg: print(f"  · {msg}", flush=True)
        )
        with open(primary_out, "w", encoding="utf-8") as f:
            f.write(report["xml_primary"])
        with open(all_out, "w", encoding="utf-8") as f:
            f.write(report["xml_all"])
        n_primary = report.get("primary_count", "?")
        n_all = report.get("marker_count", "?")
        print(f"  ✓ 완료 — 광고 {n_primary}개 / 전체후보 {n_all}개", flush=True)
        print(f"    → {primary_out}", flush=True)
        print(f"    → {all_out}", flush=True)
    except Exception as e:
        print(f"  ✗ 오류: {e}", flush=True)


def collect_mp4s(watch_dir):
    """watch_dir 및 하위 폴더에서 미처리 mp4 목록 반환."""
    pending = []
    for root, dirs, files in os.walk(watch_dir):
        # 숨김 폴더 제외
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
        for fname in sorted(files):
            if fname.endswith(".mp4") and not fname.startswith("._"):
                vpath = os.path.join(root, fname)
                if not xml_exists(vpath):
                    pending.append(vpath)
    return pending


def snapshot(watch_dir):
    """watch_dir 및 하위 폴더의 전체 파일 목록을 set으로 반환."""
    result = set()
    for root, dirs, files in os.walk(watch_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            result.add(os.path.join(root, fname))
    return result


def watch_loop(watch_dir, poll_interval):
    seen = snapshot(watch_dir)
    print(f"\n감시 중: {watch_dir} (하위 폴더 포함)", flush=True)
    print(f"폴링 간격: {poll_interval}초  |  Ctrl+C로 종료\n", flush=True)

    while True:
        time.sleep(poll_interval)
        try:
            current = snapshot(watch_dir)
        except OSError:
            continue

        new_files = current - seen
        seen = current

        for vpath in sorted(new_files):
            fname = os.path.basename(vpath)
            if fname.endswith(".mp4") and not fname.startswith("._"):
                if not xml_exists(vpath):
                    process_video(vpath)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="구글 드라이브 폴더 감시 → 자동 광고 마커 생성")
    ap.add_argument("watch_dir", help="감시할 최상위 폴더 (하위 폴더 자동 포함)")
    ap.add_argument("--interval", type=int, default=10,
                    help="폴링 간격 (초, 기본값: 10)")
    ap.add_argument("--no-scan", action="store_true",
                    help="시작 시 기존 미처리 파일 스캔 건너뜀")
    args = ap.parse_args()

    if not os.path.isdir(args.watch_dir):
        print(f"오류: 폴더가 없습니다 — {args.watch_dir}")
        sys.exit(1)

    # 시작 시 기존 미처리 파일 먼저 처리
    if not args.no_scan:
        pending = collect_mp4s(args.watch_dir)
        if pending:
            print(f"미처리 파일 {len(pending)}개 발견, 먼저 처리합니다...", flush=True)
            for vpath in pending:
                process_video(vpath)
        else:
            print("미처리 파일 없음.", flush=True)

    try:
        watch_loop(args.watch_dir, args.interval)
    except KeyboardInterrupt:
        print("\n종료", flush=True)


if __name__ == "__main__":
    main()
