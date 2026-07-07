#!/usr/bin/env python3
"""Ad Break Marker CLI (cli.py)

영상 파일을 분석하여 Premiere Pro용 XML 광고 후보 마커 파일을 생성하는 CLI 도구입니다.

사용법:
  # 기본 분석 (기본 장르: movie)
  python cli.py /path/to/video.mp4

  # 특정 장르 설정 적용 (예: 자취남)
  python cli.py /path/to/video.mp4 -g home

  # 여러 파일 일괄 분석
  python cli.py video1.mp4 video2.mp4 --genre drama

  # 파라미터 오버라이드 및 출력 경로 지정
  python cli.py video.mp4 -g movie --gap-min 8 --gap-max 12 --out-dir ./output
"""

import os
import sys
import json
import time
import argparse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analyzer import run_analysis

MIN_KEYS = ("first_min", "first_max", "gap_min", "gap_max",
            "intro_deadzone", "outro_deadzone")
FLOAT_KEYS = ("w_scene", "w_topic_change", "w_fade", "fade_silence_bonus",
              "clip_threshold", "silence_min", "w_quiet_cut")
BOOL_KEYS = ("fade_require_silence",)


def load_genres():
    genres_path = os.path.join(PROJECT_ROOT, "genres.json")
    if not os.path.exists(genres_path):
        print(f"❌ 오류: 장르 설정 파일({genres_path})을 찾을 수 없습니다.")
        sys.exit(1)
    try:
        with open(genres_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ 오류: {genres_path} 파일을 읽는 중 오류가 발생했습니다: {e}")
        sys.exit(1)


def find_genre(genres, term):
    term_lower = term.lower()
    for g in genres:
        if term_lower in (g.get("id", "").lower(), g.get("label", "").lower(), g.get("folder", "").lower()):
            return g
    return None


def print_genres_list(genres):
    print("\n사용 가능한 장르 목록:")
    print("-" * 60)
    for g in genres:
        print(f" - {g['id']:<10} | {g['label']:<6} | {g['folder_desc']}")
    print("-" * 60)


def build_settings(genre, args):
    # args의 오버라이드 값들을 모읍니다.
    overrides = {}
    
    # 분 단위 설정
    for k in MIN_KEYS:
        val = getattr(args, k, None)
        if val is not None:
            overrides[k] = val
            
    # float 설정
    for k in FLOAT_KEYS:
        val = getattr(args, k, None)
        if val is not None:
            overrides[k] = val

    # bool 설정
    if args.fade_require_silence is not None:
        overrides["fade_require_silence"] = args.fade_require_silence

    # 두 설정을 병합
    src = {**genre, **overrides}
    
    # run_analysis가 이해할 수 있는 포맷으로 변환 (분 -> 초 등)
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
            
    # fps_mode 추가
    s["fps_mode"] = args.fps_mode
    return s


def main():
    genres = load_genres()
    
    parser = argparse.ArgumentParser(
        description="자동 광고 삽입 후보 마커 생성 CLI 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="장르 정보 및 가중치 목록은 genres.json에 정의되어 있습니다."
    )
    
    parser.add_argument("videos", nargs="+", help="분석할 영상 파일 경로들")
    parser.add_argument("-g", "--genre", default="movie",
                        help="적용할 장르 (ID, 한국어 명칭, 폴더명 모두 지원. 예: movie, 영화, 자취남. 기본값: movie)")
    parser.add_argument("-o", "--out-dir",
                        help="XML 결과 파일 저장 폴더 (지정하지 않으면 영상과 같은 폴더에 저장)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="이미 XML 결과 파일이 존재하더라도 재분석 실행")
    parser.add_argument("--fps-mode", default="30", choices=["auto", "30", "29.97_ndf", "29.97_df"],
                        help="타임코드 프레임 기준 (기본값: 30)")
    
    # 오버라이드 매개변수들 (분 단위)
    parser.add_argument("--intro-deadzone", type=float, help="영상 시작 광고 제외 구간 (분)")
    parser.add_argument("--outro-deadzone", type=float, help="영상 종료 광고 제외 구간 (분)")
    parser.add_argument("--first-min", type=float, help="첫 광고 삽입 최소 시간 (분)")
    parser.add_argument("--first-max", type=float, help="첫 광고 삽입 최대 시간 (분)")
    parser.add_argument("--gap-min", type=float, help="광고 간 최소 간격 (분)")
    parser.add_argument("--gap-max", type=float, help="광고 간 최대 간격 (분)")
    
    # 가중치 오버라이드 매개변수들
    parser.add_argument("--w-scene", type=float, help="장면 전환 가중치")
    parser.add_argument("--w-topic-change", type=float, help="주제 전환 가중치")
    parser.add_argument("--w-fade", type=float, help="페이드 인/아웃 가중치")
    parser.add_argument("--w-quiet-cut", type=float, help="조용한 컷 보너스")
    parser.add_argument("--fade-silence-bonus", type=float, help="페이드에 침묵 동반 시 가산점")
    parser.add_argument("--clip-threshold", type=float, help="장면 전환 인정 CLIP 유사도 문턱")
    parser.add_argument("--silence-min", type=float, help="침묵 최소 길이 (초)")
    
    # 페이드 침묵 여부 오버라이드
    fade_sil = parser.add_mutually_exclusive_group()
    fade_sil.add_argument("--fade-require-silence", action="store_true", default=None,
                          help="페이드 마커에 침묵 필수 요구 설정 활성화")
    fade_sil.add_argument("--no-fade-require-silence", action="store_false", dest="fade_require_silence",
                          help="페이드 마커에 침묵 필수 요구 설정 비활성화")

    # 인자 파싱
    args = parser.parse_args()
    
    # 장르 찾기
    selected_genre = find_genre(genres, args.genre)
    if not selected_genre:
        print(f"❌ 오류: '{args.genre}' 장르를 찾을 수 없습니다.")
        print_genres_list(genres)
        sys.exit(1)
        
    print(f"⚙️ 적용 장르: {selected_genre['label']} ({selected_genre['id']}) - {selected_genre['ui_desc']}")
    
    # 최종 설정 딕셔너리 생성
    settings = build_settings(selected_genre, args)
    
    # 오버라이드 정보 출력
    overridden_keys = []
    all_override_keys = MIN_KEYS + FLOAT_KEYS + ("fade_require_silence",)
    for k in all_override_keys:
        if getattr(args, k, None) is not None:
            overridden_keys.append(k)
    if overridden_keys:
        print(f"🔧 커스텀 오버라이드 설정 적용됨: {', '.join(overridden_keys)}")
        
    # 처리할 비디오 파일 필터링 및 체크
    valid_videos = []
    for vpath in args.videos:
        if not os.path.exists(vpath):
            print(f"⚠️ 경고: 파일을 찾을 수 없습니다. 건너뜁니다 — {vpath}")
            continue
        valid_videos.append(vpath)
        
    if not valid_videos:
        print("❌ 오류: 처리할 유효한 영상 파일이 없습니다.")
        sys.exit(1)
        
    print(f"🎬 분석할 영상 개수: {len(valid_videos)}편\n")
    
    success_count = 0
    for idx, video_path in enumerate(valid_videos, 1):
        video_dir = args.out_dir if args.out_dir else os.path.dirname(os.path.abspath(video_path))
        if args.out_dir and not os.path.exists(args.out_dir):
            os.makedirs(args.out_dir, exist_ok=True)
            
        stem = os.path.splitext(os.path.basename(video_path))[0]
        primary_out = os.path.join(video_dir, f"{stem}_adbreaks.xml")
        all_out = os.path.join(video_dir, f"{stem}_adbreaks_all.xml")
        
        print(f"[{idx}/{len(valid_videos)}] {os.path.basename(video_path)} 분석 중...")
        
        # 파일 존재 시 스킵 처리
        if not args.force and os.path.exists(primary_out):
            print(f"  ⏭ 이미 XML 결과가 존재합니다. 건너뜁니다. (재분석은 -f 또는 --force 사용)")
            print(f"    → {primary_out}")
            success_count += 1
            continue
            
        t0 = time.time()
        
        def progress(msg):
            print(f"  · {msg}", flush=True)
            
        try:
            report = run_analysis(video_path, settings=settings, progress=progress)
            
            with open(primary_out, "w", encoding="utf-8") as f:
                f.write(report["xml_primary"])
            with open(all_out, "w", encoding="utf-8") as f:
                f.write(report["xml_all"])
                
            elapsed = time.time() - t0
            n_primary = report.get("primary_count", "?")
            n_all = sum(1 for m in report.get("markers", []) if m.get("cut_anchor"))
            print(f"  ✓ 완료 ({elapsed:.1f}초) — 1차 추천 {n_primary}개 / 전체후보 {n_all}개")
            print(f"    → {primary_out}")
            print(f"    → {all_out}")
            success_count += 1
        except Exception as e:
            print(f"  ❌ 오류 발생 ({os.path.basename(video_path)}): {e}")
            import traceback
            traceback.print_exc()
            
    print(f"\n✨ 모든 작업 완료! (성공: {success_count}/{len(valid_videos)})")


if __name__ == "__main__":
    main()
