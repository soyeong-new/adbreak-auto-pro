---
name: genre-tuning
description: Use when changing any genres.json weight or threshold (clip_threshold, w_scene, w_topic_change, silence_min, w_fade, gap_min/max…) for the ad-break tool, or when a genre's markers miss or over-fire on the editor's ground-truth ad points. Triggers — "장르 튜닝", "clip_threshold 조정", "재현율 측정", "정답 대비 측정", "_검증상태 갱신".
---

# genre-tuning — 장르 설정 변경을 정답 측정으로 검증

## 핵심 원칙

genres.json 값을 **감으로 바꾸지 않는다.** 정답(편집자가 찍은 광고 시각) 대비
재현율을 측정해 변경 전/후를 비교하고, **장르 전체 에피소드 집합**에서 개선이
확인될 때만 채택한다. 한 에피소드에 수치를 끼워맞추는 것은 과적합 — 금지.

측정은 `eval/measure_recall.py`로 한다. 영상 재처리 없이 `.cache/`의 feature
캐시로 설정만 바꿔 `local_breaks`를 재실행하므로 빠르고 재현 가능하다.

## 언제 쓰나

- genres.json의 가중치/문턱을 바꾸려 할 때 (채택 **전**에 측정)
- 어떤 장르의 마커가 정답을 놓치거나(miss) 과하게 잡힐 때(over-fire)
- 코드(local_breaks·scene_verify·점수 체계)가 바뀐 뒤 기존 `_검증상태` 숫자가
  아직 맞는지 재확인할 때 — **옛 측정값은 코드가 바뀌면 거짓이 된다**

쓰지 않을 때: UI 파라미터(분 단위 간격)만 바꾸는 경우, 정답이 없는 장르.

## 절차

1. **정답 확인** — 그 장르의 정답이 있는가?
   - 자취남 → `eval/jcn_ground_truth.txt` (`EPISODE  HH:MM:SS` 형식)
   - 그 외 → `eval/ground_truth.json` (`ground_truth`/`resolved`)
   - 정답 없으면 측정 불가 → 변경하지 말 것. 먼저 정답을 받아라.

2. **캐시 확인** — `.cache/`에 해당 에피소드의 6종 feature 캐시
   (transcript2·scenes·voice·clip_cuts·text_sim·fades)가 있어야 한다.
   없으면 그 에피소드는 자동 skip되고 표본이 줄어든다.

3. **A/B 측정** — 바꾸려는 값 하나만 override로 주고 현재값과 비교:
   ```bash
   cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
   ../.venv/bin/python eval/measure_recall.py 자취남 --set clip_threshold=0.80
   ```
   현재 genres.json 값 블록과 override 블록이 나란히 출력되고, 마지막에
   `Δ 전체 마커 재현 / Δ 1차 재현 / Δ 마커수 (n=N편)`가 찍힌다.
   여러 값 동시 비교는 `--set k=v`를 여러 번.

4. **과적합 판정** — 아래 "과적합 가드"를 통과해야 채택.

5. **genres.json 갱신** — 값을 바꾸고, 같은 항목 `_검증상태`에 **이번 측정
   결과를 사람이 읽을 한 줄로** 적는다. 표본 수·전후 수치·이유를 포함:
   ```
   "clip_threshold": "측정 기반 — JCN 30편: 0.80→0.85로 전체 재현 31%→46%(+15pp),
                      마커수 275→… . 같은 집 안 약한 컷(0.80~0.85)이 광고점이라 완화."
   ```
   `n_alternatives` 같은 무관한 키는 건드리지 않는다.

6. **재현성 메모** — 측정에 쓴 에피소드 수(n)와 날짜를 `_검증상태`에 남겨,
   나중에 코드가 바뀌어 숫자가 달라져도 무엇과 비교할지 알게 한다.

## 과적합 가드 — 채택 전 자가 점검

| 빨간불 (STOP) | 대신 |
|---|---|
| "EP06 하나에서 hit가 늘었다" | 전체 합계 Δ가 양수인지 본다 (단편 ≠ 근거) |
| 값을 0.01씩 돌려 합계 최대점을 찾음 | 규칙으로 설명되는 값만 (왜 이 장르가 약한 컷인지) |
| 표본 n이 3~4편 | 정답 있는 에피소드를 최대한 다 넣는다 |
| 전체 재현만 보고 채택 | 1차 재현·마커수도 본다 (마커 폭증 = 노이즈) |
| 문서의 옛 % 그대로 인용 | 코드 바뀌었으면 **재측정한 새 숫자**로 갱신 |

**규칙 우선:** 수치가 좋아져도 "왜 이 장르에서 이 값이 맞는가"를 한 문장으로
설명 못 하면 채택하지 않는다. (예: 자취남=같은 공간 약한 컷 → CLIP 문턱 완화)

## 측정 도구 참조

`eval/measure_recall.py <장르> [--set key=val ...]`
- 인자 없으면 현재 genres.json 설정만 측정, `--set` 주면 A/B 비교.
- hit 기준: 정답 ±`TOL`(=5.0초) 안에 마커 존재.
- settings 변환은 app.py와 동일(분 키 ×60, 가중치 그대로, clip_threshold가
  clip_real_cuts 문턱). 새 장르 정답 파일은 `GT_TXT` 매핑에 한 줄 추가.

## 흔한 실수

- **캐시 없이 측정** → 표본이 조용히 줄어든다. skip 편수 경고를 확인할 것.
- **`_검증상태` 미갱신** → 다음 사람이 옛 숫자를 신뢰. 값 바꾸면 반드시 같이 갱신.
- **1차/2차 혼동** — 자취남 광고점은 대부분 2차(전체 마커)에 잡힌다. 1차 재현이
  낮다고 실패가 아님. 그 장르가 어느 패스에서 잡히는지 먼저 이해할 것.
- **절대 수치 맹신** — % 자체보다 같은 코드에서의 전/후 Δ가 신뢰할 신호다.
- **settings=None 으로 측정** — 삭제된 옛 measure_* 스크립트들은
  `select_ad_breaks_local(..., None, ...)`로 **기본 가중치**(W_SCENE=8.0,
  W_TOPIC_CHANGE=4.0)로 쟀다. 그러면 자취남의 실제 값(w_scene=4, w_topic_change=7)이
  적용되지 않아 production(app.py→run_analysis)과 다른 숫자가 나온다.
  measure_recall.py는 genres.json의 장르 값을 그대로 넣으므로 production과 일치한다 —
  옛 `_검증상태` 숫자가 새 측정과 달라도 코드 변경이 아니라 이 차이일 수 있다.
