#!/bin/bash
# Drop Box 안에 장르별 폴더와 settings.json을 생성합니다.
# 실행: bash setup_dropbox.sh
#
# 장르 설정은 genres.json 에서 읽습니다. 수정은 genres.json 만 건드리세요.

BASE="/Volumes/guest1/Public/Drop Box"
GENRES_JSON="$(dirname "$0")/genres.json"

if [ ! -f "$GENRES_JSON" ]; then
  echo "오류: genres.json 을 찾을 수 없습니다 ($GENRES_JSON)"
  exit 1
fi

python3 - "$BASE" "$GENRES_JSON" << 'PYEOF'
import json, os, sys

base = sys.argv[1]
genres = json.loads(open(sys.argv[2]).read())

PARAM_GUIDE = {
  "first_min"      : "첫 광고 삽입 최소 시간 (분)",
  "first_max"      : "첫 광고 삽입 최대 시간 (분)",
  "gap_min"        : "광고 간 최소 간격 (분)",
  "gap_max"        : "광고 간 최대 간격 (분)",
  "w_scene"        : "장면 전환 가중치 (0~10, 높을수록 화면이 바뀌는 지점 우선)",
  "w_topic_change" : "주제 전환 가중치 (0~10, 높을수록 이야기 내용이 바뀌는 지점 우선)",
  "silence_min"    : "침묵 최소 길이 (초, 낮을수록 짧은 침묵도 감지)",
  "w_fade"         : "페이드 인/아웃 가중치 (0~10, 높을수록 암전 지점 우선)",
  "fade_require_silence" : "페이드 마커에 침묵을 필수로 요구할지 (영화·드라마·케이팝=false, 배경음 지속 장르)",
  "fade_silence_bonus"   : "페이드에 침묵이 동반될 때 가산점 (케이팝만 >0)",
  "clip_threshold"       : "장면 전환 인정 CLIP 유사도 문턱 (이 값 미만이면 진짜 전환. 기본 0.80, 자취남 0.85)",
}

for g in genres:
    folder = os.path.join(base, g["folder"])
    os.makedirs(folder, exist_ok=True)
    settings = {
        "__장르": g["label"],
        "__설명": g["folder_desc"],
        "__파라미터_안내": PARAM_GUIDE,
        "first_min"      : g["first_min"],
        "first_max"      : g["first_max"],
        "gap_min"        : g["gap_min"],
        "gap_max"        : g["gap_max"],
        "w_scene"        : g["w_scene"],
        "w_topic_change" : g["w_topic_change"],
        "silence_min"    : g["silence_min"],
        "w_fade"         : g["w_fade"],
        "fade_require_silence" : g["fade_require_silence"],
        "fade_silence_bonus"   : g["fade_silence_bonus"],
        "clip_threshold"       : g["clip_threshold"],
    }
    out = os.path.join(folder, "settings.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    print(f"  ✓ {g['folder']}/settings.json")

print(f"\n✅ 완료 — {len(genres)}개 장르 폴더 + settings.json 생성됨")
PYEOF
