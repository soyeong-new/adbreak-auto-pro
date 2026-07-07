# CLIP 재검증 정리 & 파이프라인 순서 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Path 1·2의 화면전환 판정이 CLIP 배치검증 결과를 최대한 재사용하도록 고치고, [6]CLIP 배치검증·[7]텍스트 유사도를 병렬화하고, Path 1의 데드존 밖 문장 쌍을 조기에 걸러낸다.

**Architecture:** `local_breaks.py`에 순수 함수 `_classify_scene_transition()`을 추출해 Path 1의 화면전환 판정 로직을 테스트 가능하게 만들고, `analyzer.py`에 순수 함수 `_compute_clip_and_text_signals()`을 추출해 CLIP/텍스트유사도 계산을 병렬화한다. 마커 생성 조건(침묵/허용프레임/데드존) 자체는 건드리지 않는다.

**Tech Stack:** Python 3.11, `concurrent.futures.ThreadPoolExecutor`. 테스트 프레임워크가 프로젝트에 없으므로, 각 태스크마다 `python3`로 직접 실행하는 assert 기반 검증 스크립트를 스크래치패드에 작성해 사용한다(레포에 커밋하지 않음).

## Global Constraints

- 허용 프레임: 30fps 기준 :00/:01/:02/:03/:28/:29만 허용, 스냅 없음 — 이번 변경에서 프레임 판정 로직 자체는 건드리지 않는다.
- CLIP 임계값 `SAME_THRESHOLD=0.80`(장르별 override 가능), 텍스트 유사도 임계값 `TEXT_SIM_THRESHOLD=0.75` — 값 변경 없음.
- 마커가 생성되는 조건(침묵 존재·허용 프레임 존재·데드존 밖)은 어떤 태스크에서도 변경하지 않는다. 최종 마커 목록·XML 출력은 수정 전후 동일해야 한다.
- 재현율 베이스라인(수정 전, `eval/measure_recall.py`로 측정, 캐시 기반이라 재처리 없음):
  - 자취남: 전체 마커 재현 18/39(46%), 1차 선발 재현 2/39(5%), 마커수 445
  - 드라마: 전체 마커 재현 27/32(84%), 1차 선발 재현 6/32(19%), 마커수 1100
  - 모든 태스크 완료 후 이 두 숫자가 **정확히 동일**해야 한다(다르면 회귀 발생).
- `select_ad_breaks_local()`을 직접 호출하는 곳은 `analyzer.py`와 `eval/measure_recall.py` 단 두 곳뿐이다(grep으로 확인됨). 시그니처를 바꾸는 태스크는 이 두 호출부를 함께 갱신해야 한다.

---

### Task 1: `_classify_scene_transition` 헬퍼 추출 + Path 1 화면전환 판정 로직 교체

**Files:**
- Modify: `local_breaks.py:273-296`(시그니처), `local_breaks.py:347-357`(판정 블록)
- Test: 스크래치패드 스크립트 (레포에 커밋하지 않음)

**Interfaces:**
- Produces: `_classify_scene_transition(marker_time, cuts, real_cuts, checked_cuts) -> (has_cut: bool, cut_dist: float, clip_preconfirmed: bool)` — Task 4(Path 2)는 이 함수를 쓰지 않지만, 같은 파일의 `select_ad_breaks_local()` 시그니처에 `clip_checked_cuts=None` 파라미터가 추가된 것을 Task 2가 의존함.

- [ ] **Step 1: 실패하는 테스트 작성**

`/private/tmp/claude-501/-Users-choisoyeong-Desktop-vscode-adbreak-auto-pro/e4b9e12c-8da2-4723-9144-6780e09cbe6c/scratchpad/test_classify.py` 작성:

```python
import sys
sys.path.insert(0, "/Users/choisoyeong/Desktop/vscode/adbreak_auto_pro")
from local_breaks import _classify_scene_transition


def test_confirmed_real_cut_within_radius():
    has_cut, dist, preconfirmed = _classify_scene_transition(
        marker_time=100.0, cuts=[100.2], real_cuts={100.2}, checked_cuts={100.2})
    assert has_cut is True
    assert preconfirmed is True
    assert abs(dist - 0.2) < 1e-9


def test_confirmed_fake_cut_within_radius():
    has_cut, dist, preconfirmed = _classify_scene_transition(
        marker_time=100.0, cuts=[100.2], real_cuts=set(), checked_cuts={100.2})
    assert has_cut is False
    assert preconfirmed is False


def test_unchecked_cut_within_radius_defers_to_individual_verify():
    has_cut, dist, preconfirmed = _classify_scene_transition(
        marker_time=100.0, cuts=[100.2], real_cuts=set(), checked_cuts=set())
    assert has_cut is True
    assert preconfirmed is False
    assert abs(dist - 0.2) < 1e-9


def test_cut_outside_radius_ignored():
    has_cut, dist, preconfirmed = _classify_scene_transition(
        marker_time=100.0, cuts=[102.0], real_cuts={102.0}, checked_cuts={102.0})
    assert has_cut is False
    assert preconfirmed is False


def test_no_cuts_at_all():
    has_cut, dist, preconfirmed = _classify_scene_transition(
        marker_time=100.0, cuts=[], real_cuts=set(), checked_cuts=set())
    assert has_cut is False


TESTS = [
    test_confirmed_real_cut_within_radius,
    test_confirmed_fake_cut_within_radius,
    test_unchecked_cut_within_radius_defers_to_individual_verify,
    test_cut_outside_radius_ignored,
    test_no_cuts_at_all,
]

for fn in TESTS:
    fn()
    print(f"PASS: {fn.__name__}")
print("ALL PASS")
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `../.venv/bin/python /private/tmp/.../scratchpad/test_classify.py`
Expected: `ImportError: cannot import name '_classify_scene_transition' from 'local_breaks'`

- [ ] **Step 3: `local_breaks.py`에 헬퍼 추가 + 시그니처 확장**

`local_breaks.py:296` 바로 다음 줄(빈 줄) 뒤에 헬퍼 함수 추가 (모듈 레벨, `select_ad_breaks_local` 함수 정의보다 앞, 예를 들어 `_nearest_allowed_frame` 함수 정의 다음인 `local_breaks.py:194` 부근):

```python
def _classify_scene_transition(marker_time, cuts, real_cuts, checked_cuts):
    """마커 시각에서 가장 가까운 원본 컷을 찾아 화면전환 여부를 판정한다.

    real_cuts   : CLIP 배치검증에서 진짜 장면전환으로 확인된 컷 집합.
    checked_cuts: CLIP 배치검증을 시도해서 값이 나온 컷 전체 집합(real_cuts의 상위집합).
                  real_cuts에 없지만 checked_cuts엔 있으면 "확인했는데 가짜"라는 뜻이고,
                  checked_cuts에도 없으면 "배치검증 자체가 안 됨"(데드존 경계 등)이라는 뜻이다.

    Returns (has_cut, cut_dist, clip_preconfirmed).
    """
    if not cuts:
        return False, 0.0, False
    cut = min(cuts, key=lambda c: abs(c - marker_time))
    dist = abs(cut - marker_time)
    if dist > SCENE_RADIUS_CLIP:
        return False, 0.0, False
    if cut in real_cuts:
        return True, dist, True
    if cut in checked_cuts:
        return False, 0.0, False
    return True, dist, False
```

`local_breaks.py:273-277`의 시그니처를 다음으로 교체:

```python
def select_ad_breaks_local(segments, duration, settings=None,
                           scene_cuts=None, voice_env=None,
                           loudness_env=None,
                           clip_real_cuts=None, clip_checked_cuts=None,
                           text_sims=None,
                           fade_cuts=None, fps=FPS, drop_frame=False):
```

`local_breaks.py:296` 다음 줄에 추가:

```python
    checked_cuts = set(clip_checked_cuts) if clip_checked_cuts else set()
```

`local_breaks.py:347-357`의 기존 블록:

```python
        has_cut, cut_dist = False, 0.0
        clip_preconfirmed = False
        if cuts:
            cut = min(cuts, key=lambda c: abs(c - marker_time))
            dist = abs(cut - marker_time)
            if dist <= SCENE_RADIUS:
                has_cut, cut_dist = True, dist
            elif real_cuts and dist <= SCENE_RADIUS_CLIP and cut in real_cuts:
                has_cut, cut_dist = True, dist
                clip_preconfirmed = True
```

를 다음으로 교체:

```python
        has_cut, cut_dist, clip_preconfirmed = _classify_scene_transition(
            marker_time, cuts, real_cuts, checked_cuts)
```

(`SCENE_RADIUS` 상수 자체는 삭제하지 않는다 — Path 2·Path 3의 중복 방지 체크에서 별도로 계속 쓰인다.)

- [ ] **Step 4: 테스트 재실행해서 통과 확인**

Run: `../.venv/bin/python /private/tmp/.../scratchpad/test_classify.py`
Expected: `ALL PASS`

- [ ] **Step 5: 재현율 확인 (Task 1 단독 상태 — 완전한 베이스라인 일치는 Task 2 이후에 확인)**

Run: `/Users/choisoyeong/Desktop/vscode/.venv/bin/python eval/measure_recall.py 자취남` — `clip_checked_cuts`가 `eval/measure_recall.py`에서 아직 안 넘어가(Task 2에서 배선 예정) 항상 빈 집합이라, `real_cuts`에 없는 근접 컷은 전부 "데이터 없음"(3번 분기)으로 처리되어 `SCENE_RADIUS_CLIP`(1.0초)까지 `has_cut=True`가 적용된다. 자취남은 베이스라인과 **정확히 동일**해야 한다(18/39·2/39·445) — 실제로 동일함을 확인함.

**주의**: 같은 이유로 드라마 재현율은 이 단계에서 1차 선발 재현이 6/32가 아니라 **7/32로 나오는 게 정상**이다(전체 마커 재현 27/32·마커수 1100은 동일 — `has_cut`은 순위매기기에만 영향을 주고 마커 존재 자체엔 영향 없기 때문). `clip_checked_cuts`가 실제 데이터로 채워지는 Task 2가 끝나야 3번 분기가 "데드존 경계 등 진짜 데이터 없는 극소수 컷"으로만 좁혀져 드라마도 베이스라인(6/32)과 정확히 일치한다 — 검증 완료(Task 2용 배선을 임시 적용 후 재측정해 6/32로 돌아옴을 확인함). Task 1 단독 완료 조건은 자취남 일치 + 드라마 전체재현/마커수 일치이며, 드라마 1차재현 최종 일치는 Task 2의 완료 조건이다.

- [ ] **Step 6: 커밋**

```bash
git add local_breaks.py
git commit -m "refactor: extract _classify_scene_transition helper for testability"
```

---

### Task 2: `analyzer.py` — `clip_checked_cuts` 계산 + 호출부·`eval/measure_recall.py` 갱신

**Files:**
- Modify: `analyzer.py:124-137`(clip_sims/text_sims 계산), `analyzer.py:141-147`(호출부)
- Modify: `eval/measure_recall.py:144-150`(`run_one` 함수)
- Test: 스크래치패드 스크립트

**Interfaces:**
- Consumes: Task 1의 `select_ad_breaks_local(..., clip_checked_cuts=None, ...)` 시그니처
- Produces: `analyzer.py`가 `clip_checked_cuts`를 계산해 넘기므로, 이후 실제 분석에서 Task 1의 "확인했는데 가짜" 분기가 처음으로 실제 데이터를 받게 됨

- [ ] **Step 1: `analyzer.py`에 `clip_checked_cuts` 계산 추가**

`analyzer.py:124-137`의 기존 코드:

```python
    clip_sims = {}
    clip_real_cuts = set()
    if valid_cuts:
        clip_sims = batch_scene_similarities(video_path, valid_cuts,
                                             progress=progress)
        clip_real_cuts = {c for c, sim in clip_sims.items()
                          if sim is not None and sim < clip_th}

    # 텍스트 의미 유사도: CLIP 확인된 컷 전후 주제가 바뀌는지 측정.
    # 낮은 유사도 = 주제 전환 = 광고 후보로 우선 고려.
    text_sims = {}
    if valid_cuts:
        text_sims = batch_text_similarities(video_path, segments, valid_cuts,
                                            progress=progress)
```

를 다음으로 교체:

```python
    clip_sims = {}
    clip_real_cuts = set()
    clip_checked_cuts = set()
    if valid_cuts:
        clip_sims = batch_scene_similarities(video_path, valid_cuts,
                                             progress=progress)
        clip_real_cuts = {c for c, sim in clip_sims.items()
                          if sim is not None and sim < clip_th}
        clip_checked_cuts = {c for c, sim in clip_sims.items()
                             if sim is not None}

    # 텍스트 의미 유사도: CLIP 확인된 컷 전후 주제가 바뀌는지 측정.
    # 낮은 유사도 = 주제 전환 = 광고 후보로 우선 고려.
    text_sims = {}
    if valid_cuts:
        text_sims = batch_text_similarities(video_path, segments, valid_cuts,
                                            progress=progress)
```

`analyzer.py:141-147`의 호출부:

```python
    markers = select_ad_breaks_local(segments, duration, settings,
                                     scene_cuts=scenes, voice_env=voice,
                                     loudness_env=loudness,
                                     clip_real_cuts=clip_real_cuts,
                                     text_sims=text_sims,
                                     fade_cuts=fades,
                                     fps=fps, drop_frame=drop_frame)
```

에 `clip_checked_cuts=clip_checked_cuts,` 한 줄 추가 (`clip_real_cuts=clip_real_cuts,` 다음 줄):

```python
    markers = select_ad_breaks_local(segments, duration, settings,
                                     scene_cuts=scenes, voice_env=voice,
                                     loudness_env=loudness,
                                     clip_real_cuts=clip_real_cuts,
                                     clip_checked_cuts=clip_checked_cuts,
                                     text_sims=text_sims,
                                     fade_cuts=fades,
                                     fps=fps, drop_frame=drop_frame)
```

- [ ] **Step 2: `eval/measure_recall.py`도 동일하게 갱신 (누락 시 측정값이 부정확해짐)**

`eval/measure_recall.py:144-150`의 기존 코드:

```python
def run_one(stem, settings):
    segs, dur, scenes, voice, loudness, clip, tsim, fades = load_inputs(stem)
    clip_th = float(settings.get("clip_threshold", SAME_THRESHOLD))
    clip_real = {c for c, s in clip.items() if s is not None and s < clip_th}
    markers = select_ad_breaks_local(segs, dur, settings, scene_cuts=scenes,
                                     voice_env=voice, loudness_env=loudness,
                                     clip_real_cuts=clip_real,
                                     text_sims=tsim, fade_cuts=fades)
```

를 다음으로 교체:

```python
def run_one(stem, settings):
    segs, dur, scenes, voice, loudness, clip, tsim, fades = load_inputs(stem)
    clip_th = float(settings.get("clip_threshold", SAME_THRESHOLD))
    clip_real = {c for c, s in clip.items() if s is not None and s < clip_th}
    clip_checked = {c for c, s in clip.items() if s is not None}
    markers = select_ad_breaks_local(segs, dur, settings, scene_cuts=scenes,
                                     voice_env=voice, loudness_env=loudness,
                                     clip_real_cuts=clip_real,
                                     clip_checked_cuts=clip_checked,
                                     text_sims=tsim, fade_cuts=fades)
```

- [ ] **Step 3: 재현율 회귀 확인 — 이번엔 진짜 3분기가 다 작동함**

Run: `../.venv/bin/python eval/measure_recall.py 자취남` 그리고 `../.venv/bin/python eval/measure_recall.py 드라마`
Expected: 베이스라인과 **정확히 동일**(자취남 18/39·2/39·445, 드라마 27/32·6/32·1100). `_verify()`(개별 CLIP 재검증)는 `eval/measure_recall.py`가 호출하지 않으므로, 마커 생성 단계의 `has_cut` 값이 최종 결과에 직결된다 — 그래서 이 숫자가 정확히 같아야만 Task 1의 3분기 로직이 기존 동작을 보존한다는 증거가 된다.

다르게 나오면: Task 1의 `_classify_scene_transition` 분기 로직이나 이번 Step의 배선이 잘못됐다는 뜻이므로 진행하지 말고 원인을 찾는다.

- [ ] **Step 4: 커밋**

```bash
git add analyzer.py eval/measure_recall.py
git commit -m "feat: wire clip_checked_cuts through analyzer and eval tooling"
```

---

### Task 3: `analyzer.py` — [6]CLIP 배치검증·[7]텍스트 유사도 병렬화

**Files:**
- Modify: `analyzer.py` (Task 2에서 수정한 블록을 함수로 추출)
- Test: 스크래치패드 스크립트 (모델 호출을 스텁으로 대체, 실제 영상 불필요)

**Interfaces:**
- Produces: `_compute_clip_and_text_signals(video_path, valid_cuts, segments, clip_th, progress=None) -> (clip_real_cuts: set, clip_checked_cuts: set, clip_sims: dict, text_sims: dict)` — `clip_sims`(원본 유사도 딕셔너리)를 같이 반환하는 이유는 `analyzer.py:151-154`의 기존 "clip_preconfirmed 마커에 유사도 부착" 블록이 `clip_sims`를 그대로 참조하기 때문.

- [ ] **Step 1: 실패하는 테스트 작성**

`/private/tmp/.../scratchpad/test_parallel_signals.py`:

```python
import sys
import time
sys.path.insert(0, "/Users/choisoyeong/Desktop/vscode/adbreak_auto_pro")
import analyzer


def fake_clip(video_path, cuts, progress=None):
    time.sleep(0.3)
    return {c: 0.5 for c in cuts}


def fake_text(video_path, segments, cuts, progress=None):
    time.sleep(0.3)
    return {c: 0.9 for c in cuts}


original_clip = analyzer.batch_scene_similarities
original_text = analyzer.batch_text_similarities
analyzer.batch_scene_similarities = fake_clip
analyzer.batch_text_similarities = fake_text
try:
    start = time.time()
    real_cuts, checked_cuts, clip_sims, text_sims = analyzer._compute_clip_and_text_signals(
        "dummy.mp4", [10.0, 20.0], [], clip_th=0.80)
    elapsed = time.time() - start

    assert elapsed < 0.5, f"병렬 실행 기대(<0.5s), 실제 {elapsed:.2f}s (순차면 ~0.6s)"
    assert checked_cuts == {10.0, 20.0}, checked_cuts
    assert real_cuts == {10.0, 20.0}, real_cuts  # sim=0.5 < clip_th=0.80
    assert clip_sims == {10.0: 0.5, 20.0: 0.5}, clip_sims
    assert text_sims == {10.0: 0.9, 20.0: 0.9}, text_sims

    empty_real, empty_checked, empty_sims, empty_text = analyzer._compute_clip_and_text_signals(
        "dummy.mp4", [], [], clip_th=0.80)
    assert empty_real == set() and empty_checked == set()
    assert empty_sims == {} and empty_text == {}

    print("PASS: parallel execution + empty-input handling")
finally:
    analyzer.batch_scene_similarities = original_clip
    analyzer.batch_text_similarities = original_text
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `../.venv/bin/python /private/tmp/.../scratchpad/test_parallel_signals.py`
Expected: `AttributeError: module 'analyzer' has no attribute '_compute_clip_and_text_signals'`

- [ ] **Step 3: `analyzer.py`에 헬퍼 추가 + `run_analysis()`에서 사용**

`analyzer.py` 상단 import 구역(`from concurrent.futures import ThreadPoolExecutor` 바로 다음)에 헬퍼 함수 추가:

```python
def _compute_clip_and_text_signals(video_path, valid_cuts, segments, clip_th, progress=None):
    """[6]CLIP 배치검증과 [7]텍스트 유사도를 병렬로 계산한다.

    둘 다 valid_cuts만 입력받는 독립적인 계산이라 동시 실행 가능하다.
    Returns (clip_real_cuts, clip_checked_cuts, clip_sims, text_sims).
    """
    if not valid_cuts:
        return set(), set(), {}, {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_clip = pool.submit(batch_scene_similarities, video_path, valid_cuts,
                             progress=progress)
        f_text = pool.submit(batch_text_similarities, video_path, segments,
                             valid_cuts, progress=progress)
        clip_sims = f_clip.result()
        text_sims = f_text.result()
    clip_real_cuts = {c for c, sim in clip_sims.items()
                      if sim is not None and sim < clip_th}
    clip_checked_cuts = {c for c, sim in clip_sims.items() if sim is not None}
    return clip_real_cuts, clip_checked_cuts, clip_sims, text_sims
```

Task 2에서 만든 `analyzer.py:124-138` 블록(clip_sims/clip_real_cuts/clip_checked_cuts/text_sims 계산 전체)을 다음으로 교체:

```python
    clip_real_cuts, clip_checked_cuts, clip_sims, text_sims = _compute_clip_and_text_signals(
        video_path, valid_cuts, segments, clip_th, progress=progress)
```

`clip_sims`는 `analyzer.py:151-154`의 기존 블록이 그대로 참조한다 — 이 블록은 변경 없음:

```python
    # Attach batch CLIP similarity to clip_preconfirmed markers.
    for m in markers:
        if m.get("clip_preconfirmed") and clip_sims:
            nearest_cut = min(clip_sims, key=lambda c: abs(c - m["time"]))
            if abs(nearest_cut - m["time"]) < 1.5:
                m["clip_similarity"] = clip_sims[nearest_cut]
```

- [ ] **Step 4: 테스트 재실행해서 통과 확인**

Run: `../.venv/bin/python /private/tmp/.../scratchpad/test_parallel_signals.py`
Expected: `PASS: parallel execution + empty-input handling`

- [ ] **Step 5: 재현율 회귀 확인**

Run: `../.venv/bin/python eval/measure_recall.py 자취남` / `드라마` — 베이스라인과 동일해야 함(`eval/measure_recall.py`는 이 헬퍼를 안 거치지만, `analyzer.py`의 `run_analysis()` 경로가 여전히 올바르게 동작하는지는 실제 영상으로 확인이 필요함 — 캐시된 영상 하나를 골라 `app.py`나 `cli.py`로 직접 돌려서 에러 없이 끝나는지, 결과 마커 수가 이전과 같은지 확인).

- [ ] **Step 6: 커밋**

```bash
git add analyzer.py
git commit -m "perf: parallelize CLIP batch verification and text similarity computation"
```

---

### Task 4: Path 2 `cut_anchor` 마커에 `clip_preconfirmed` 플래그 추가

**Files:**
- Modify: `local_breaks.py` (Path 2 마커 딕셔너리, `"cut_anchor": True,` 라인 부근)
- Test: 스크래치패드 스크립트

- [ ] **Step 1: 실패하는 테스트 작성**

`/private/tmp/.../scratchpad/test_path2_preconfirmed.py`:

```python
import sys
sys.path.insert(0, "/Users/choisoyeong/Desktop/vscode/adbreak_auto_pro")
from framecode import frame_to_seconds
from local_breaks import select_ad_breaks_local

cut_t = frame_to_seconds(301, 30)  # 10.0333...초, ff=1 -> 허용 프레임

segments = [
    {"start": 0.0, "end": 10.0, "text": "안녕하세요 오늘은 여기까지입니다."},
    {"start": 10.6, "end": 20.0, "text": "자 이제 다음 이야기를 시작하겠습니다."},
]

markers = select_ad_breaks_local(
    segments, duration=30.0,
    settings={"intro_deadzone": 0.0, "outro_deadzone": 0.0},
    scene_cuts=[cut_t], voice_env=None, loudness_env=None,
    clip_real_cuts={cut_t}, clip_checked_cuts={cut_t},
    text_sims={}, fade_cuts=None, fps=30)

cut_anchor_markers = [m for m in markers if m.get("cut_anchor")]
assert len(cut_anchor_markers) == 1, f"expected 1 cut_anchor marker, got {len(cut_anchor_markers)}"
assert cut_anchor_markers[0]["clip_preconfirmed"] is True

print("PASS: Path2 cut_anchor marker has clip_preconfirmed=True")
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `../.venv/bin/python /private/tmp/.../scratchpad/test_path2_preconfirmed.py`
Expected: `KeyError: 'clip_preconfirmed'` (필드가 아직 없음)

- [ ] **Step 3: Path 2 마커 딕셔너리에 필드 추가**

`local_breaks.py`에서 Path 2 블록의 마커 딕셔너리(`"cut_anchor": True,`가 있는 곳)를 찾아:

```python
            m = {
                "time": marker_time,
                "frame": frame,
                "timecode": frame_to_timecode(frame, fps, drop_frame),
                "tier": frame_tier(frame, fps, drop_frame),
                "has_cut": True,
                "has_signal": signal,
                "score": round(sc, 2),
                "reason": "; ".join(reasons),
                "ended_sentence": ended_text,
                "next_sentence": nxt_text,
                "kill_reason": kill_reason,
                "cut_anchor": True,
            }
```

를 다음으로 교체(`"cut_anchor": True,` 다음 줄에 `"clip_preconfirmed": True,` 추가):

```python
            m = {
                "time": marker_time,
                "frame": frame,
                "timecode": frame_to_timecode(frame, fps, drop_frame),
                "tier": frame_tier(frame, fps, drop_frame),
                "has_cut": True,
                "has_signal": signal,
                "score": round(sc, 2),
                "reason": "; ".join(reasons),
                "ended_sentence": ended_text,
                "next_sentence": nxt_text,
                "kill_reason": kill_reason,
                "cut_anchor": True,
                "clip_preconfirmed": True,
            }
```

(Path 2는 애초에 `real_cuts`에서만 컷을 뽑으므로 항상 `True`로 고정해도 안전하다 — 조건부로 넣을 필요 없음.)

- [ ] **Step 4: 테스트 재실행해서 통과 확인**

Run: `../.venv/bin/python /private/tmp/.../scratchpad/test_path2_preconfirmed.py`
Expected: `PASS: Path2 cut_anchor marker has clip_preconfirmed=True`

- [ ] **Step 5: 재현율 회귀 확인**

Run: `../.venv/bin/python eval/measure_recall.py 자취남` / `드라마` — 베이스라인과 동일해야 함 (`clip_preconfirmed`는 마커 생성 자체엔 영향 없고, `_verify()`의 재검증 스킵 여부에만 영향을 주는데 `measure_recall.py`는 `_verify()`를 안 부르므로 이 숫자는 변화가 없는 게 정상).

- [ ] **Step 6: 커밋**

```bash
git add local_breaks.py
git commit -m "fix: mark Path2 cut-anchor markers as clip_preconfirmed to skip redundant CLIP re-verification"
```

---

### Task 5: Path 1 데드존 조기 스킵

**Files:**
- Modify: `local_breaks.py` (Path 1 루프 시작 부분, `for i in range(len(sentences) - 1):` 다음)
- Test: 스크래치패드 스크립트

- [ ] **Step 1: 실패하는 테스트 작성**

`/private/tmp/.../scratchpad/test_deadzone_early_skip.py`:

```python
import sys
sys.path.insert(0, "/Users/choisoyeong/Desktop/vscode/adbreak_auto_pro")
import local_breaks
from local_breaks import select_ad_breaks_local

call_count = {"n": 0}
original_find_silence = local_breaks._find_silence


def counting_find_silence(*args, **kwargs):
    call_count["n"] += 1
    return original_find_silence(*args, **kwargs)


local_breaks._find_silence = counting_find_silence

segments = [
    {"start": 49.0, "end": 50.0, "text": "이건 데드존 이전 문장입니다."},
    {"start": 51.0, "end": 52.0, "text": "이것도 데드존 이전 문장입니다."},
    {"start": 149.0, "end": 150.0, "text": "이건 유효 구간 문장입니다."},
    {"start": 151.0, "end": 152.0, "text": "이것도 유효 구간 문장입니다."},
    {"start": 249.0, "end": 250.0, "text": "이건 데드존 이후 문장입니다."},
    {"start": 251.0, "end": 252.0, "text": "이것도 데드존 이후 문장입니다."},
]

rate = 100
duration = 260.0
n = int(duration * rate)
db = [-20.0] * n


def mark_quiet(t0, t1):
    for i in range(int(t0 * rate), int(t1 * rate)):
        db[i] = -60.0


mark_quiet(50.0, 51.0)
mark_quiet(150.0, 151.0)
mark_quiet(250.0, 251.0)
voice_env = {"db": db, "rate": rate}

settings = {"intro_deadzone": 100.0, "outro_deadzone": 60.0}  # lo=100, hi=260-60=200

try:
    markers = select_ad_breaks_local(
        segments, duration, settings,
        scene_cuts=[], voice_env=voice_env, loudness_env=None,
        clip_real_cuts=set(), clip_checked_cuts=set(),
        text_sims={}, fade_cuts=None)
finally:
    local_breaks._find_silence = original_find_silence

assert call_count["n"] == 1, f"데드존 밖 2쌍은 스킵돼야 함, 실제 호출 {call_count['n']}회"
assert len(markers) == 1, f"expected exactly 1 marker, got {len(markers)}"
assert 100.0 <= markers[0]["time"] <= 200.0

print("PASS: deadzone early-skip reduces silence search calls")
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `../.venv/bin/python /private/tmp/.../scratchpad/test_deadzone_early_skip.py`
Expected: `AssertionError: 데드존 밖 2쌍은 스킵돼야 함, 실제 호출 3회` (현재는 3쌍 다 `_find_silence`를 호출함)

- [ ] **Step 3: Path 1 루프에 조기 스킵 추가**

`local_breaks.py`에서 Path 1 루프의 다음 부분:

```python
    for i in range(len(sentences) - 1):
        ended, nxt = sentences[i], sentences[i + 1]

        # A real silence must follow the completed sentence -- otherwise the
        # speech runs straight through and this is not a true sentence break.
        sil = _find_silence(voice_env, ended["end"] - SILENCE_SEARCH,
                            nxt["start"] + SILENCE_SEARCH, noise_floor,
                            min_dur=_sil_min)
```

를 다음으로 교체(`ended, nxt = ...` 다음 줄에 조기 스킵 추가):

```python
    for i in range(len(sentences) - 1):
        ended, nxt = sentences[i], sentences[i + 1]

        # 데드존 조기 스킵: 침묵 탐색 범위 전체가 데드존 밖이면 값싼 비교만으로
        # 걸러내고, 뒤쪽의 무거운 침묵 탐색(_find_silence)을 건너뛴다. 범위가
        # 걸쳐 있는 경우는 여기서 걸러지지 않고 통과 -- 최종 정밀 판정은
        # marker_time 확정 후 아래(관문 3)에서 그대로 수행한다.
        if nxt["start"] + SILENCE_SEARCH < lo or ended["end"] - SILENCE_SEARCH > hi:
            continue

        # A real silence must follow the completed sentence -- otherwise the
        # speech runs straight through and this is not a true sentence break.
        sil = _find_silence(voice_env, ended["end"] - SILENCE_SEARCH,
                            nxt["start"] + SILENCE_SEARCH, noise_floor,
                            min_dur=_sil_min)
```

- [ ] **Step 4: 테스트 재실행해서 통과 확인**

Run: `../.venv/bin/python /private/tmp/.../scratchpad/test_deadzone_early_skip.py`
Expected: `PASS: deadzone early-skip reduces silence search calls`

- [ ] **Step 5: 재현율 회귀 확인 (최종 확인)**

Run: `../.venv/bin/python eval/measure_recall.py 자취남` / `드라마`
Expected: 베이스라인과 **정확히 동일**(자취남 18/39·2/39·445, 드라마 27/32·6/32·1100). 다섯 태스크가 모두 끝난 뒤의 최종 검증이므로, 여기서 숫자가 달라지면 전체 변경 중 어딘가 회귀가 있다는 뜻이다.

- [ ] **Step 6: 커밋**

```bash
git add local_breaks.py
git commit -m "perf: skip silence search early for sentence pairs entirely outside the deadzone"
```

---

## 최종 확인 체크리스트

- [ ] `eval/measure_recall.py 자취남`, `드라마` 결과가 베이스라인과 동일
- [ ] 캐시된 영상 하나를 `cli.py`로 직접 돌려서 에러 없이 끝까지 실행되는지 확인 (`_verify()`가 실제로 개별 CLIP을 얼마나 적게 부르는지도 `progress` 로그로 눈으로 확인 가능)
- [ ] 스크래치패드 테스트 스크립트들은 레포에 커밋하지 않았는지 확인 (`git status`)
