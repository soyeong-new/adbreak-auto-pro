"""AIPP_17의 자취남 추가 16편 분석 (정답은 있으나 미분석이던 것).
S01_EP01, S02_EP01~08, S03_EP02~10.
"""
import os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from analyzer import run_analysis

BASE = "/Volumes/AIPP_17/자취남"
EPISODES = [
    "JCN_S01_EP01_HD_KR",
    "JCN_S02_EP01_HD_KR", "JCN_S02_EP02_HD_KR", "JCN_S02_EP03_HD_KR",
    "JCN_S02_EP04_HD_KR", "JCN_S02_EP05_HD_KR", "JCN_S02_EP06_HD_KR",
    "JCN_S02_EP07_HD_KR", "JCN_S02_EP08_HD_KR",
    "JCN_S03_EP02_HD_KR", "JCN_S03_EP03_HD_KR", "JCN_S03_EP04_HD_KR",
    "JCN_S03_EP05_HD_KR", "JCN_S03_EP08_HD_KR", "JCN_S03_EP09_HD_KR",
    "JCN_S03_EP10_HD_KR",
]


def main():
    settings = {"w_scene": 4, "w_topic_change": 7, "silence_min": 0.5,
                "w_fade": 3, "fade_require_silence": True, "fade_silence_bonus": 0}
    ok = fail = 0
    for i, ep in enumerate(EPISODES, 1):
        path = os.path.join(BASE, ep + ".mp4")
        if not os.path.exists(path):
            print(f"[{i}/{len(EPISODES)}] 없음: {path}", flush=True); fail += 1; continue
        t = time.time()
        try:
            print(f"[{i}/{len(EPISODES)}] 분석 시작: {ep}", flush=True)
            run_analysis(path, settings, progress=lambda m: None)
            print(f"[{i}/{len(EPISODES)}] 완료 ({time.time()-t:.0f}s)", flush=True)
            ok += 1
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[{i}/{len(EPISODES)}] 실패: {e}", flush=True); fail += 1
    print(f"\n=== 완료: 성공 {ok} / 실패 {fail} ===", flush=True)


if __name__ == "__main__":
    main()
