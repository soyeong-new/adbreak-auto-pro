"""extract_features.py가 생성한 피처 행렬로 점수 모델 학습 (train_score.py)

모델:
  1. 로지스틱 회귀 — 해석 가능. 계수가 local_breaks._score()의 가중치 상수와 직접 대응.
  2. 랜덤 포레스트 — 성능 비교용. 피처 중요도도 제공.

검증:
  - 에피소드(ep 컬럼) 기준 GroupKFold 5겹 교차 검증.
    같은 에피소드가 훈련/테스트에 동시에 포함되지 않음.
  - 주 지표: PR-AUC (불균형 데이터에 적합한 정밀도-재현율 곡선).
  - ROC-AUC, precision@recall=0.5, 임계값별 PR 표도 함께 보고.

클래스 불균형:
  label=1이 전체의 약 3% → 두 모델 모두 class_weight='balanced' 적용.

출력:
  eval/output/train_report.json  — 전체 결과 (가중치, CV 점수, PR 표)
  요약 내용을 stdout에 출력
"""
from __future__ import annotations

import json
import math
import os
import sys
import warnings
from typing import List

warnings.filterwarnings("ignore")

EVAL_DIR    = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT= os.path.dirname(EVAL_DIR)
DEFAULT_FEATURES = os.path.join(EVAL_DIR, "output", "features.json")
DEFAULT_OUT      = os.path.join(EVAL_DIR, "output", "train_report.json")

FEATURE_COLS = [
    "score",
    "has_cut",
    "scene_cut_det",
    "clip_not_cut",
    "clip_passed",
    "clip_sim",
    "cut_dist",
    "silence_db",
    "long_silence",
    "frame_00",
    "strong_opener",
    "weak_opener",
    "closer",
    "short_prev",
    "qa",
    "continuation",
    "cta",
    "text_sim",
    "topic_change",
]


def load_dataset(path: str):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)

    X, y, groups = [], [], []
    used = 0
    skipped = 0
    for row in rows:
        feats = []
        ok = True
        for col in FEATURE_COLS:
            v = row.get(col)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                # Fill NaN: clip_sim → 0, cut_dist → 0 (no cut), silence_db → 0
                if col in ("clip_sim", "cut_dist"):
                    feats.append(0.0)
                elif col == "silence_db":
                    feats.append(0.0)
                else:
                    feats.append(0)
            elif isinstance(v, bool):
                feats.append(int(v))
            else:
                feats.append(float(v))
        X.append(feats)
        y.append(int(row["label"]))
        groups.append(row["ep"])
        used += 1

    return X, y, groups, used


def cv_evaluate(model_cls, model_kwargs: dict,
                X, y, groups, n_splits: int = 5):
    """GroupKFold CV. Returns per-fold and aggregate PR/ROC scores."""
    from sklearn.model_selection import GroupKFold
    from sklearn.metrics import (average_precision_score, roc_auc_score,
                                  precision_recall_curve)
    import numpy as np

    X = np.array(X, dtype=float)
    y = np.array(y, dtype=int)
    groups = np.array(groups)

    unique_eps = list(dict.fromkeys(groups))
    actual_splits = min(n_splits, len(unique_eps))
    gkf = GroupKFold(n_splits=actual_splits)

    fold_results = []
    all_y_true, all_y_prob = [], []

    for fold, (train_idx, test_idx) in enumerate(gkf.split(X, y, groups)):
        X_tr, X_te = X[train_idx], X[test_idx]
        y_tr, y_te = y[train_idx], y[test_idx]

        clf = model_cls(**model_kwargs)
        clf.fit(X_tr, y_tr)
        y_prob = clf.predict_proba(X_te)[:, 1]

        all_y_true.extend(y_te.tolist())
        all_y_prob.extend(y_prob.tolist())

        if y_te.sum() == 0:
            fold_results.append({"fold": fold, "pr_auc": None, "roc_auc": None,
                                  "n_pos": 0, "n_neg": int((y_te==0).sum())})
            continue

        pr_auc  = float(average_precision_score(y_te, y_prob))
        try:
            roc_auc = float(roc_auc_score(y_te, y_prob))
        except Exception:
            roc_auc = None

        fold_results.append({
            "fold": fold,
            "pr_auc":  round(pr_auc, 4),
            "roc_auc": round(roc_auc, 4) if roc_auc else None,
            "n_pos": int(y_te.sum()),
            "n_neg": int((y_te == 0).sum()),
        })

    # aggregate over folds that have positives
    valid = [f for f in fold_results if f["pr_auc"] is not None]
    mean_pr  = sum(f["pr_auc"]  for f in valid) / len(valid) if valid else None
    mean_roc = (sum(f["roc_auc"] for f in valid if f["roc_auc"])
                / sum(1 for f in valid if f["roc_auc"])) if valid else None

    # full PR curve over all folds
    prec_arr, rec_arr, thr_arr = precision_recall_curve(all_y_true, all_y_prob)
    overall_pr_auc = float(average_precision_score(all_y_true, all_y_prob))

    # PR table: sample at recall thresholds 0.1, 0.2, ..., 0.9
    pr_table = []
    for target_rec in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        # find highest precision where recall >= target
        candidates = [(p, r) for p, r in zip(prec_arr, rec_arr) if r >= target_rec]
        if candidates:
            p, r = max(candidates, key=lambda x: x[0])
            pr_table.append({"recall": target_rec, "precision": round(float(p), 3),
                             "actual_recall": round(float(r), 3)})

    return {
        "folds":           fold_results,
        "mean_pr_auc":     round(mean_pr, 4)  if mean_pr  else None,
        "mean_roc_auc":    round(mean_roc, 4) if mean_roc else None,
        "overall_pr_auc":  round(overall_pr_auc, 4),
        "pr_table":        pr_table,
        "n_folds_with_pos": len(valid),
    }


def train_full(model_cls, model_kwargs: dict, X, y):
    """Train on full dataset. Returns fitted model."""
    import numpy as np
    clf = model_cls(**model_kwargs)
    clf.fit(np.array(X, dtype=float), np.array(y, dtype=int))
    return clf


def lr_coefficients(clf, feature_names: List[str]) -> List[dict]:
    coefs = clf.coef_[0].tolist()
    intercept = float(clf.intercept_[0])
    result = [{"feature": f, "coef": round(c, 4)}
              for f, c in zip(feature_names, coefs)]
    result.sort(key=lambda x: abs(x["coef"]), reverse=True)
    result.append({"feature": "__intercept__", "coef": round(intercept, 4)})
    return result


def rf_importances(clf, feature_names: List[str]) -> List[dict]:
    imps = clf.feature_importances_.tolist()
    result = [{"feature": f, "importance": round(i, 4)}
              for f, i in zip(feature_names, imps)]
    result.sort(key=lambda x: x["importance"], reverse=True)
    return result


def print_summary(lr_cv, rf_cv, lr_coefs, rf_imps, n_pos, n_neg):
    print("=" * 65)
    print(f"데이터셋: {n_pos}개 label=1 (GT hit), {n_neg}개 label=0 (miss)")
    print(f"클래스 불균형 비율: 1:{n_neg//max(n_pos,1)}")
    print("=" * 65)

    for name, cv in [("Logistic Regression", lr_cv), ("Random Forest", rf_cv)]:
        print(f"\n▶ {name}")
        print(f"  CV PR-AUC  (mean over folds with +):  "
              f"{cv['mean_pr_auc']  if cv['mean_pr_auc']  else 'N/A'}")
        print(f"  CV ROC-AUC (mean):                    "
              f"{cv['mean_roc_auc'] if cv['mean_roc_auc'] else 'N/A'}")
        print(f"  Overall PR-AUC (pooled predictions):  {cv['overall_pr_auc']}")
        print(f"  Folds with positives: {cv['n_folds_with_pos']}/5")
        if cv["pr_table"]:
            print(f"  PR table (precision @ recall threshold):")
            print(f"    {'recall':>7}  {'precision':>9}")
            for row in cv["pr_table"]:
                print(f"    {row['recall']:>7.1f}  {row['precision']:>9.3f}")

    print("\n▶ Logistic Regression 계수 (크기순):")
    print(f"  {'feature':20s}  {'coef':>8}  {'방향'}")
    for row in lr_coefs[:12]:
        if row["feature"] == "__intercept__":
            continue
        direction = "↑ hit" if row["coef"] > 0 else "↓ hit"
        print(f"  {row['feature']:20s}  {row['coef']:>8.4f}  {direction}")

    print("\n▶ Random Forest feature importance (상위 10개):")
    for row in rf_imps[:10]:
        bar = "█" * int(row["importance"] * 100)
        print(f"  {row['feature']:20s}  {row['importance']:.4f}  {bar}")
    print("=" * 65)


def _cli():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--features", default=DEFAULT_FEATURES)
    p.add_argument("--out",      default=DEFAULT_OUT)
    p.add_argument("--folds",    type=int, default=5)
    args = p.parse_args()

    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import RandomForestClassifier
    except ImportError:
        print("scikit-learn 없음. pip install scikit-learn --break-system-packages")
        sys.exit(1)

    print(f"features 로드 중: {args.features}")
    X, y, groups, n_used = load_dataset(args.features)
    n_pos = sum(y)
    n_neg = n_used - n_pos
    print(f"  {n_used}개 마커, label=1: {n_pos}, label=0: {n_neg}, "
          f"에피소드: {len(set(groups))}편")

    if n_pos < 5:
        print(f"⚠ label=1이 {n_pos}개뿐. CV 결과 신뢰도 낮음.")

    lr_kwargs = dict(class_weight="balanced", max_iter=1000, random_state=42,
                     solver="lbfgs")
    rf_kwargs = dict(n_estimators=200, class_weight="balanced",
                     max_depth=6, random_state=42, n_jobs=-1)

    print(f"\nLogistic Regression {args.folds}-fold CV 실행 중...")
    lr_cv = cv_evaluate(LogisticRegression, lr_kwargs, X, y, groups, args.folds)

    print(f"Random Forest {args.folds}-fold CV 실행 중...")
    rf_cv = cv_evaluate(RandomForestClassifier, rf_kwargs, X, y, groups, args.folds)

    # train on full dataset for coefficient / importance extraction
    print("전체 데이터로 최종 모델 학습 중...")
    lr_full = train_full(LogisticRegression, lr_kwargs, X, y)
    rf_full = train_full(RandomForestClassifier, rf_kwargs, X, y)

    lr_coefs = lr_coefficients(lr_full, FEATURE_COLS)
    rf_imps  = rf_importances(rf_full, FEATURE_COLS)

    print_summary(lr_cv, rf_cv, lr_coefs, rf_imps, n_pos, n_neg)

    # write report
    report = {
        "n_markers":   n_used,
        "n_pos":       n_pos,
        "n_neg":       n_neg,
        "n_episodes":  len(set(groups)),
        "feature_cols": FEATURE_COLS,
        "logistic_regression": {
            "cv":          lr_cv,
            "coefficients": lr_coefs,
        },
        "random_forest": {
            "cv":               rf_cv,
            "feature_importance": rf_imps,
        },
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 결과 저장: {args.out}")


if __name__ == "__main__":
    _cli()
