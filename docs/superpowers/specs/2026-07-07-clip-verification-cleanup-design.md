# 마커 후보 생성 — CLIP 재검증 정리 & 파이프라인 순서 개선

## 배경

마커 후보 생성 파이프라인(`analyzer.py` → `local_breaks.py`)을 정독하는 과정에서 세 가지
비효율/오류를 발견했다.

1. **CLIP 배치검증 결과 미활용** — `analyzer.py`는 컷마다 CLIP 유사도를 미리 계산해
   `clip_real_cuts`(진짜 장면전환 컷)를 만들어두는데, `local_breaks.py`의 Path 1(침묵 기반)
   과 Path 2(컷 앵커)는 이 결과를 제대로 참고하지 않고 있었다.
   - Path 1: 마커와 0.3초 이내로 가까운 컷은 CLIP 배치검증 결과와 무관하게 무조건
     `has_cut=True`로 표시했다가, 분석 마지막 단계(`_verify()`)에서 개별 CLIP을 다시 돌려
     확인한다. 이미 배치검증에서 "가짜"로 판정 난 컷이어도 다시 확인하는 중복 계산이다.
   - Path 2: `cut_anchor` 마커는 애초에 CLIP 배치검증을 통과한 컷(`real_cuts`)에서만
     생성되는데도, `clip_preconfirmed` 플래그가 안 붙어 있어 `_verify()`가 또 개별 CLIP을
     돌린다. `_verify()`의 docstring은 "cut-anchor 마커는 재검증 안 함"이라고 되어 있지만
     실제 동작은 그렇지 않다 — 주석과 코드가 어긋나 있다.

2. **[6]CLIP 배치검증과 [7]텍스트 유사도가 순차 실행** — `analyzer.py`에서 이 둘은 서로의
   결과를 쓰지 않는 독립적인 계산인데(둘 다 `valid_cuts`만 입력으로 받음) 순차로 실행되고
   있다. [1]~[5](Whisper·PySceneDetect·voice_env·loudness_env·fade_cuts)처럼 병렬 실행이
   가능하다.

3. **Path 1의 데드존 체크 순서** — 문장 쌍마다 침묵 탐색(`_find_silence`) → 허용 프레임
   탐색(`_allowed_frame_in`) → 데드존 체크 순으로 진행되는데, 데드존 밖 문장 쌍도 앞의
   두 계산을 다 거친 뒤에야 버려진다. 데드존 여부를 먼저(값싸게) 걸러내면 불필요한 침묵
   탐색을 줄일 수 있다.

세 항목 모두 **마커가 생성되는 조건(문장 사이 침묵 + 허용 프레임 + 데드존)** 자체는
건드리지 않는다. 최종 마커 목록과 XML 출력 결과는 수정 전후로 동일해야 한다.

## 목표

- CLIP 배치검증에서 이미 나온 결과를 Path 1·Path 2가 일관되게 재사용하도록 고쳐서,
  `_verify()`의 불필요한 개별 CLIP 재검증 호출을 최소화한다.
- [6]CLIP 배치검증과 [7]텍스트 유사도를 병렬 실행한다.
- Path 1의 문장 쌍 검사 순서를 바꿔, 데드존 밖은 침묵 탐색 전에 걸러낸다.

## 범위 밖 (Out of scope)

- 마커 생성 조건(침묵 최소 길이, 허용 프레임 정의, 데드존 길이 등) 자체 변경 없음
- CLIP 임계값(`SAME_THRESHOLD=0.80`), 텍스트 유사도 임계값(`0.75`) 등 상수 변경 없음
- Path 3(페이드) 로직 변경 없음
- `_verify()`의 마지막 개별 재검증 메커니즘 자체(틀리면 점수 회수) 변경 없음 — 그 대상이
  줄어들 뿐, 로직은 유지

---

## 설계 1 — CLIP 배치검증 결과 재사용 (Path 1 / Path 2)

### 새 집합: `clip_checked_cuts`

`analyzer.py`에서 `clip_sims`(컷별 CLIP 유사도, 측정 실패 시 `None`)를 계산한 뒤, 기존
`clip_real_cuts`(진짜로 확인된 컷)에 더해 **"확인을 시도해서 값이 나온 컷 전체"**를 별도로
계산한다.

```python
clip_real_cuts    = {c for c, sim in clip_sims.items() if sim is not None and sim < clip_th}
clip_checked_cuts = {c for c, sim in clip_sims.items() if sim is not None}
```

`clip_real_cuts`는 항상 `clip_checked_cuts`의 부분집합이다. 프레임 추출 실패(`sim is None`)
는 "확인 안 됨"과 동일하게 취급한다 — `is_real_scene_change()`의 기존 정책(측정 불가 시
버리지 않고 살려둠)과 일관성을 맞추기 위함이다.

이 두 집합을 `select_ad_breaks_local()` 호출부에 인자로 추가 전달한다
(`clip_real_cuts`는 기존에 이미 전달 중, `clip_checked_cuts`만 신규 추가).

### Path 1 — has_cut 판정 로직 교체

기존에는 `SCENE_RADIUS`(0.3초)/`SCENE_RADIUS_CLIP`(1.0초) 두 반경으로 나뉘어 있었으나,
반경 통합 후에는 두 반경의 내부 로직이 완전히 동일해지므로 **하나의 반경
(`SCENE_RADIUS_CLIP`, 1.0초)** 으로 통합한다.

마커 시각(`marker_time`)에서 가장 가까운 원본 컷(`cut`)을 찾은 뒤:

| 조건 | 판정 |
|---|---|
| `cut in real_cuts` (배치검증: 진짜) | `has_cut=True`, `clip_preconfirmed=True` — 재검증 생략 |
| `cut in clip_checked_cuts`이고 `real_cuts`엔 없음 (배치검증: 가짜) | `has_cut=False` — 애초에 화면전환 딱지 안 붙임, 재검증 생략 |
| `cut`이 `clip_checked_cuts`에도 없음 (배치검증 데이터 없음 — 데드존 경계 등) | `has_cut=True`, `clip_preconfirmed=False` — 기존과 동일하게 `_verify()`에서 개별 재검증 |

세 번째 분기(데이터 없음)는 기존 동작을 그대로 유지한다 — 일단 `has_cut=True`로 점수를
매기고, 나중에 개별 CLIP이 "가짜"로 판정하면 점수를 회수하는 기존 `_verify()` 로직을
재사용한다(추가 코드 불필요, 회수 방향과 반대인 "합격 시 가산" 로직을 새로 만들 필요가
없어 더 단순함).

### Path 2 — clip_preconfirmed 플래그 추가

`cut_anchor` 마커는 애초에 `real_cuts`에서만 생성되므로(이미 진짜로 확인된 컷), 마커
딕셔너리 생성 시 `m["clip_preconfirmed"] = True`를 추가한다. 이러면:

- `_verify()`의 기존 필터 조건(`has_cut and not clip_preconfirmed and not fade_anchor`)에
  자동으로 걸려 재검증 대상에서 제외됨 — `_verify()` 코드 변경 불필요.
- `analyzer.py`에 이미 있는 "clip_preconfirmed 마커에 유사도 부착" 블록이 자동으로 Path 2
  마커에도 적용되어 `clip_similarity`/reason 문구가 채워짐 — 이 부분도 코드 변경 불필요.

### 영향받는 파일

- `analyzer.py`: `clip_checked_cuts` 계산 추가, `select_ad_breaks_local()` 호출부에 인자
  추가
- `local_breaks.py`: `select_ad_breaks_local()` 시그니처에 `clip_checked_cuts` 파라미터
  추가, Path 1의 has_cut 판정 블록 교체, Path 2의 마커 딕셔너리에 필드 1개 추가

---

## 설계 2 — [6]CLIP 배치검증과 [7]텍스트 유사도 병렬화

`analyzer.py`에서 현재:

```python
clip_sims = batch_scene_similarities(video_path, valid_cuts, progress=progress)
clip_real_cuts = {...}
text_sims = batch_text_similarities(video_path, segments, valid_cuts, progress=progress)
```

이 둘은 서로의 출력을 쓰지 않고 각자 `valid_cuts`만 입력받으므로, [1]~[5]와 동일한 방식
(`ThreadPoolExecutor`)으로 병렬 실행한다.

```python
if valid_cuts:
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_clip = pool.submit(batch_scene_similarities, video_path, valid_cuts, progress=progress)
        f_text = pool.submit(batch_text_similarities, video_path, segments, valid_cuts, progress=progress)
        clip_sims = f_clip.result()
        text_sims = f_text.result()
else:
    clip_sims, text_sims = {}, {}

clip_real_cuts    = {c for c, sim in clip_sims.items() if sim is not None and sim < clip_th}
clip_checked_cuts = {c for c, sim in clip_sims.items() if sim is not None}
```

### 영향받는 파일

- `analyzer.py`만 수정.

---

## 설계 3 — Path 1: 데드존 체크를 침묵 탐색보다 먼저

현재 순서(문장 쌍마다):
1. 침묵 탐색(`_find_silence`)
2. 허용 프레임 탐색(`_allowed_frame_in`)
3. `marker_time` 확정
4. 데드존 체크 (`lo <= marker_time <= hi`)

`marker_time`은 침묵 탐색 이후에만 정확히 알 수 있으므로, 정밀한 데드존 체크는 여전히
4번 자리에서 해야 한다. 다만 **"이 문장 쌍의 침묵 탐색 범위 전체가 데드존 밖에 있는가"**
는 침묵 탐색 전에 값싸게 미리 걸러낼 수 있다.

```python
for i in range(len(sentences) - 1):
    ended, nxt = sentences[i], sentences[i + 1]

    # 침묵 탐색 범위(SILENCE_SEARCH 여유 포함)가 통째로 데드존 밖이면 조기 스킵
    if nxt["start"] + SILENCE_SEARCH < lo or ended["end"] - SILENCE_SEARCH > hi:
        continue

    sil = _find_silence(...)
    ...
    # 이후 marker_time 확정 후의 정밀 데드존 체크(4번)는 그대로 유지
```

이 조기 스킵은 "범위 전체가 밖에 있을 때만" 걸러내므로 결과에 영향이 없다(안전한
최적화). 정밀 체크(4번)는 그대로 남겨둔다 — 조기 스킵을 통과했어도 실제 `marker_time`은
경계에 걸칠 수 있기 때문.

### 영향받는 파일

- `local_breaks.py`: Path 1 루프 맨 앞에 조기 스킵 조건 추가.

---

## 검증 방법

이 프로젝트엔 별도 유닛테스트가 없다. `eval/measure_recall.py`(정답 대비 재현율 측정)가
기존 검증 수단이므로, 수정 전후로 같은 영상에 대해 재현율을 재서 **변화가 없는지**
확인한다(마커 생성 조건 자체는 안 바꿨으므로 원칙적으로 동일해야 함). 추가로
`_verify()`의 개별 CLIP 호출 횟수를 수정 전후로 비교해, 실제로 줄어드는지 확인한다.
