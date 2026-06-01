"""자취남(JCN) 정답 보유 14편을 새로 분석해 캐시 생성.

이전 29.97 캐시는 쓰지 않기 위해, 대상 에피소드의 기존 캐시를 지우고 새로 만든다.
실행: ../.venv/bin/python eval/analyze_jcn.py
"""
import os, sys, glob, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from analyzer import run_analysis

BASE = "/Volumes/AIPP22/자취남"
EPISODES = [
    "JCN_S01_EP02_HD_KR", "JCN_S01_EP03_HD_KR", "JCN_S01_EP04_HD_KR",
    "JCN_S01_EP05_HD_KR", "JCN_S01_EP06_HD_KR", "JCN_S01_EP07_HD_KR",
    "JCN_S04_EP02_HD_KR", "JCN_S04_EP03_HD_KR", "JCN_S04_EP04_HD_KR",
    "JCN_S04_EP06_HD_KR", "JCN_S04_EP08_HD_KR",
    "JCN_S05_EP01_HD_KR", "JCN_S06_EP01_HD_KR", "JCN_S07_EP01_HD_KR",
]
CACHE = os.path.join(ROOT, ".cache")


def main():
    # 1) 대상 에피소드의 기존(29.97) 캐시 제거
    removed = 0
    for ep in EPISODES:
        for f in glob.glob(os.path.join(CACHE, ep + "_*")):
            os.remove(f); removed += 1
    print(f"기존 캐시 {removed}개 제거 (29.97 캐시 정리)", flush=True)

    # 2) 자취남 설정으로 새 분석 (캐시 데이터는 설정과 무관하지만 동일 조건 유지)
    settings = {
        "w_scene": 4, "w_topic_change": 7, "silence_min": 0.5, "w_fade": 3,
        "fade_require_silence": True, "fade_silence_bonus": 0,
    }
    ok, fail = 0, 0
    for i, ep in enumerate(EPISODES, 1):
        path = os.path.join(BASE, ep + ".mp4")
        if not os.path.exists(path):
            print(f"[{i}/{len(EPISODES)}] 없음: {path}", flush=True)
            fail += 1
            continue
        t = time.time()
        try:
            print(f"[{i}/{len(EPISODES)}] 분석 시작: {ep}", flush=True)
            run_analysis(path, settings,
                         progress=lambda m: print(f"    · {m}", flush=True))
            print(f"[{i}/{len(EPISODES)}] 완료 ({time.time()-t:.0f}s)", flush=True)
            ok += 1
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[{i}/{len(EPISODES)}] 실패: {e}", flush=True)
            fail += 1
    print(f"\n=== 분석 완료: 성공 {ok} / 실패 {fail} ===", flush=True)


if __name__ == "__main__":
    main()
