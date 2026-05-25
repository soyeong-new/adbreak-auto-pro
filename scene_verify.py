"""Semantic scene-cut verification with CLIP.

A pixel-difference detector (PySceneDetect) cannot tell a real scene transition
from a jump cut or a framing change within the same setup. CLIP encodes what an
image *is* (a classroom, two people at a desk...), so a wide shot and a close-up
of the same setup get similar embeddings while a genuinely new scene does not.

Two modes:
  is_real_scene_change()      — single-cut check (used in _verify for marker confirmation)
  batch_scene_similarities()  — all cuts at once (used in analyzer to find real transitions
                                 before candidate generation so cut-anchor path works)
"""
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

_model = None
_preprocess = None

# CLIP image-image cosine similarity. Above SAME_THRESHOLD => same scene
# (jump cut / framing change / graphic overlay); below => a real scene transition.
# Frame-verified across 13 episodes: real transitions score 0.69-0.77, while
# same-scene-with-graphics false positives start at ~0.79. 0.78 is conservative
# -- it rejects every observed false positive (the user's main complaint).
SAME_THRESHOLD = 0.78


def _load():
    global _model, _preprocess
    if _model is None:
        import open_clip
        _model, _, _preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32-quickgelu", pretrained="openai")
        _model.eval()
    return _model, _preprocess


def _extract_frame(video_path, t, out_path):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(max(0.0, t)), "-i", video_path,
         "-frames:v", "1", "-q:v", "3", "-y", out_path],
        capture_output=True)
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def embed_image(img_path):
    import torch
    from PIL import Image
    model, preprocess = _load()
    img = preprocess(Image.open(img_path).convert("RGB")).unsqueeze(0)
    with torch.no_grad():
        feat = model.encode_image(img)
        feat /= feat.norm(dim=-1, keepdim=True)
    return feat


def image_similarity(path_a, path_b):
    """Cosine similarity of two image files' CLIP embeddings."""
    fa, fb = embed_image(path_a), embed_image(path_b)
    return float((fa @ fb.T).item())


def scene_similarity(video_path, cut_time, offset=0.3):
    """Similarity between the shot just before and just after a cut.

    High => same scene (jump cut/framing change). Low => real scene transition.
    Returns None if frames could not be extracted.
    """
    with tempfile.TemporaryDirectory() as d:
        a = os.path.join(d, "a.jpg")
        b = os.path.join(d, "b.jpg")
        ok_a = _extract_frame(video_path, cut_time - offset, a)
        ok_b = _extract_frame(video_path, cut_time + offset, b)
        if not (ok_a and ok_b):
            return None
        return image_similarity(a, b)


def is_real_scene_change(video_path, cut_time, offset=0.3):
    """True if the cut is a genuine scene transition (not a jump cut)."""
    sim = scene_similarity(video_path, cut_time, offset)
    if sim is None:
        return True, None          # can't verify -> don't discard
    return sim < SAME_THRESHOLD, sim


# ---------------------------------------------------------------------------
# Batch mode: compute CLIP similarities for many cuts at once, with caching.
# Used by analyzer.py to find all real scene transitions before candidate
# generation so the cut-anchor path in local_breaks.py can use them.
# ---------------------------------------------------------------------------

def _clip_cache_path(video_path):
    """Return the path for the per-video CLIP similarity cache."""
    from pipeline import CACHE_DIR
    import os as _os
    st = _os.stat(video_path)
    key = f"{_os.path.splitext(_os.path.basename(video_path))[0]}_{st.st_size}"
    return _os.path.join(CACHE_DIR, f"{key}.clip_cuts.json")


def batch_scene_similarities(video_path, cut_times, offset=0.3,
                              max_workers=6, progress=None):
    """Compute CLIP similarity for every cut in *cut_times*.

    Returns a dict  {cut_time (float): similarity (float | None)}.

    Results are cached next to the other pipeline caches so re-running the
    same video is instant.  Only the cuts not already in the cache are sent
    through CLIP.
    """
    if not cut_times:
        return {}

    cache_path = _clip_cache_path(video_path)
    # Load existing cache (keys are stored as strings).
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cached = {float(k): v for k, v in json.load(f).items()}
    else:
        cached = {}

    missing = [c for c in cut_times if c not in cached]
    if not missing:
        return {c: cached[c] for c in cut_times}

    if progress:
        progress(f"장면 전환 CLIP 분석 중 ({len(missing)}개)...")

    # Extract frames for all missing cuts in parallel, then embed in batch.
    # We write frames to a single shared temp directory; each cut gets two
    # files named by its rounded-millisecond timestamp.
    results = dict(cached)  # start from cache

    import torch
    from PIL import Image

    with tempfile.TemporaryDirectory() as d:
        # --- step 1: extract frames (parallel ffmpeg) ---
        def extract_pair(cut_t):
            ta = str(max(0.0, cut_t - offset))
            tb = str(cut_t + offset)
            pa = os.path.join(d, f"{cut_t:.3f}_a.jpg")
            pb = os.path.join(d, f"{cut_t:.3f}_b.jpg")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", ta, "-i", video_path,
                 "-frames:v", "1", "-q:v", "3", "-y", pa],
                capture_output=True)
            subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", tb, "-i", video_path,
                 "-frames:v", "1", "-q:v", "3", "-y", pb],
                capture_output=True)
            ok = (os.path.exists(pa) and os.path.getsize(pa) > 0 and
                  os.path.exists(pb) and os.path.getsize(pb) > 0)
            return cut_t, pa, pb, ok

        pairs = {}   # cut_t -> (path_a, path_b)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {pool.submit(extract_pair, c): c for c in missing}
            for fut in as_completed(futs):
                cut_t, pa, pb, ok = fut.result()
                if ok:
                    pairs[cut_t] = (pa, pb)
                else:
                    results[cut_t] = None   # couldn't extract → treat as unknown

        # --- step 2: embed all frames in one batch ---
        if pairs:
            model, preprocess = _load()
            cuts_ordered = sorted(pairs)
            imgs_a, imgs_b = [], []
            for c in cuts_ordered:
                pa, pb = pairs[c]
                try:
                    imgs_a.append(preprocess(Image.open(pa).convert("RGB")))
                    imgs_b.append(preprocess(Image.open(pb).convert("RGB")))
                except Exception:
                    imgs_a.append(None)
                    imgs_b.append(None)

            # Filter out None entries (failed opens)
            valid_idx = [i for i, (a, b) in enumerate(zip(imgs_a, imgs_b))
                         if a is not None and b is not None]
            if valid_idx:
                batch_a = torch.stack([imgs_a[i] for i in valid_idx])
                batch_b = torch.stack([imgs_b[i] for i in valid_idx])
                with torch.no_grad():
                    fa = model.encode_image(batch_a)
                    fb = model.encode_image(batch_b)
                    fa = fa / fa.norm(dim=-1, keepdim=True)
                    fb = fb / fb.norm(dim=-1, keepdim=True)
                    sims = (fa * fb).sum(dim=-1).tolist()
                for rank, vi in enumerate(valid_idx):
                    results[cuts_ordered[vi]] = round(sims[rank], 4)
            # Failed opens → None
            for i, c in enumerate(cuts_ordered):
                if i not in valid_idx:
                    results[c] = None

    # Persist updated cache
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in results.items()}, f)

    return {c: results.get(c) for c in cut_times}
