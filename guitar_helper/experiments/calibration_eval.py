"""EXP-001 harness: mean vs median tone archetype calibration.

Read-only experiment. Does NOT touch archetypes.json, the segments table, or any
production code path. See docs/experiments/EXP-001-mean-vs-median-calibration.md.

Usage:
    python -m guitar_helper.experiments.calibration_eval [--db library.db]
        [--stems-dir stems] [--music-dir music] [--out results/exp-001] [--diagnose]
"""
from __future__ import annotations

import argparse
import itertools
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.analysis.feature_extractor import FeatureExtractor
from guitar_helper.analysis.tone_classifier import (
    _CONFIDENCE_FLOOR,
    TONE_LABELS,
    ThresholdClassifier,
)
from guitar_helper.db.schema import init_db

_HOP = 512
_NEAR_SILENT_RMS = 0.02  # normalized clf-space rms (raw rms / 0.25) below this = near-silent
_OUTLIER_IQR_K = 1.5
_OUTLIER_STD_K = 2.0
_MIN_MEMBERS = 2  # tones with fewer members after a hold-out fall back to DEFAULT

_TONES = [t for t in TONE_LABELS if t != "other"]
_DEFAULT = {k: np.asarray(v, dtype=np.float64)
            for k, v in ThresholdClassifier.DEFAULT_ARCHETYPES.items()}

# Feature row layout (24 dims): 0 flatness, 1 zcr, 2 rms, 3 centroid, 4:11 contrast(7), 11:24 mfcc(13)
_FEATURE_NAMES = (
    ["flatness", "zcr", "rms", "centroid"]
    + [f"contrast{i}" for i in range(7)]
    + [f"mfcc{i}" for i in range(13)]
)
_ACTIVE = {"flatness": 0, "zcr": 1, "rms": 2, "centroid": 3, "contrast": slice(4, 11)}


# ---------------------------------------------------------------------------
# Statistic primitives (the experimental factors)
# ---------------------------------------------------------------------------
def reduce_frames(matrix_slice: np.ndarray, stat: str) -> np.ndarray:
    return matrix_slice.mean(axis=1) if stat == "mean" else np.median(matrix_slice, axis=1)


def aggregate(vecs: list[np.ndarray], stat: str) -> np.ndarray:
    arr = np.asarray(vecs, dtype=np.float64)
    return arr.mean(axis=0) if stat == "mean" else np.median(arr, axis=0)


def distance(a: np.ndarray, b: np.ndarray, metric: str) -> float:
    d = a - b
    return float(np.linalg.norm(d)) if metric == "l2" else float(np.abs(d).sum())


def classify(vec: np.ndarray, archetypes: dict[str, np.ndarray], metric: str) -> tuple[str, float]:
    dists = {lbl: distance(vec, arch, metric) for lbl, arch in archetypes.items()}
    best = min(dists, key=dists.__getitem__)
    worst = max(dists.values())
    conf = (1.0 - dists[best] / worst) if worst > 0 else 1.0
    if conf < _CONFIDENCE_FLOOR:
        return "other", conf
    return best, conf


# ---------------------------------------------------------------------------
# Data loading (reuses run_calibrate logic)
# ---------------------------------------------------------------------------
def _find_audio(file_hash, filename, stems_dir, music_dir):
    stem = stems_dir / f"{file_hash}_guitar.wav"
    if stem.exists():
        return stem
    if music_dir is not None:
        candidate = music_dir / filename
        if candidate.exists():
            return candidate
    return None


def load_segment_frames(db, stems_dir, music_dir):
    """Return list of dicts: {tone, frames (24,n), filename, start_ms, end_ms, hash}."""
    rows = db.execute("""
        SELECT s.file_hash, s.start_ms, s.end_ms, s.tone_label, t.filename
        FROM segments s JOIN tracks t ON s.file_hash = t.file_hash
        WHERE s.manually_corrected = 1
        ORDER BY s.tone_label, s.file_hash, s.start_ms
    """).fetchall()

    loader, extractor = AudioLoader(), FeatureExtractor(hop_length=_HOP)
    clf_cache: dict[str, np.ndarray] = {}
    sr_cache: dict[str, int] = {}
    missing: set[str] = set()
    segments = []

    for file_hash, start_ms, end_ms, tone, filename in rows:
        if file_hash in missing:
            continue
        if file_hash not in clf_cache:
            path = _find_audio(file_hash, filename, stems_dir, music_dir)
            if path is None:
                print(f"  WARNING: no audio for {filename} ({file_hash[:12]}) — skipping")
                missing.add(file_hash)
                continue
            y, sr = loader.load_mono(str(path))
            clf_cache[file_hash] = extractor.extract_for_classification(y, sr)
            sr_cache[file_hash] = sr

        clf, sr = clf_cache[file_hash], sr_cache[file_hash]
        n = clf.shape[1]
        f0 = max(0, min(int(round(start_ms / 1000 * sr / _HOP)), n - 1))
        f1 = max(f0 + 1, min(int(round(end_ms / 1000 * sr / _HOP)), n))
        segments.append({
            "tone": tone, "frames": clf[:, f0:f1], "filename": filename,
            "start_ms": start_ms, "end_ms": end_ms, "hash": file_hash,
        })
    return segments


# ---------------------------------------------------------------------------
# LOOCV evaluation
# ---------------------------------------------------------------------------
def build_archetypes(seg_vecs, exclude_idx, cross_stat):
    groups = defaultdict(list)
    for i, (tone, vec) in enumerate(seg_vecs):
        if i == exclude_idx or tone == "other":
            continue
        groups[tone].append(vec)
    arch = {}
    for tone in _TONES:
        if len(groups.get(tone, [])) >= _MIN_MEMBERS:
            arch[tone] = aggregate(groups[tone], cross_stat)
        else:
            arch[tone] = _DEFAULT[tone]
    return arch


def _metrics(true, pred, conf):
    labels = list(TONE_LABELS)
    cm = {t: {p: 0 for p in labels} for t in labels}
    for tr, pr in zip(true, pred):
        cm[tr][pr] += 1

    def prf(label):
        tp = cm[label][label]
        fp = sum(cm[t][label] for t in labels) - tp
        fn = sum(cm[label][p] for p in labels) - tp
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        return prec, rec, f1, tp + fn

    per_tone = {t: prf(t) for t in labels}
    supported = [t for t in labels if per_tone[t][3] > 0]
    macro_f1 = float(np.mean([per_tone[t][2] for t in supported])) if supported else 0.0

    n = len(true)
    acc_all = sum(t == p for t, p in zip(true, pred)) / n if n else 0.0
    no_other = [(t, p) for t, p in zip(true, pred) if t != "other"]
    acc_no_other = (sum(t == p for t, p in no_other) / len(no_other)) if no_other else 0.0

    ec = cm["edge"]["crunch"] + cm["crunch"]["edge"]
    ec_support = per_tone["edge"][3] + per_tone["crunch"][3]
    ec_rate = ec / ec_support if ec_support else 0.0

    correct_conf = [c for t, p, c in zip(true, pred, conf) if t == p]
    wrong_conf = [c for t, p, c in zip(true, pred, conf) if t != p]
    return {
        "accuracy_all": acc_all, "accuracy_no_other": acc_no_other, "macro_f1": macro_f1,
        "edge_crunch_confusion": ec_rate, "per_tone": per_tone, "confusion": cm,
        "conf_correct": float(np.mean(correct_conf)) if correct_conf else 0.0,
        "conf_wrong": float(np.mean(wrong_conf)) if wrong_conf else 0.0,
    }


def run_condition(segments, frame_stat, cross_stat, metric):
    seg_vecs = [(s["tone"], reduce_frames(s["frames"], frame_stat)) for s in segments]
    true, pred, conf = [], [], []
    for i, (tone, vec) in enumerate(seg_vecs):
        arch = build_archetypes(seg_vecs, i, cross_stat)
        label, c = classify(vec, arch, metric)
        true.append(tone)
        pred.append(label)
        conf.append(c)
    return _metrics(true, pred, conf)


def archetype_separation(segments, frame_stat, cross_stat, metric):
    seg_vecs = [(s["tone"], reduce_frames(s["frames"], frame_stat)) for s in segments]
    arch = build_archetypes(seg_vecs, -1, cross_stat)
    pairs = {}
    for a, b in itertools.combinations(_TONES, 2):
        pairs[f"{a}-{b}"] = distance(arch[a], arch[b], metric)
    return pairs


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------
def diagnose(segments):
    by_tone = defaultdict(list)
    near_silent = []
    for s in segments:
        vec = s["frames"].mean(axis=1)
        by_tone[s["tone"]].append((vec, s))
        rms_row = s["frames"][2]
        frac = float(np.mean(rms_row < _NEAR_SILENT_RMS))
        if frac > 0.10:
            near_silent.append((frac, s))

    lines = ["# EXP-001 Outlier Diagnostic\n"]
    for tone in sorted(by_tone):
        vecs = np.array([v for v, _ in by_tone[tone]])
        lines.append(f"\n## {tone}  (n={len(vecs)})\n")
        lines.append("| feature | mean | median | std | IQR | min | max |")
        lines.append("|---|---|---|---|---|---|---|")
        for name, idx in _ACTIVE.items():
            col = vecs[:, idx].mean(axis=1) if isinstance(idx, slice) else vecs[:, idx]
            q1, q3 = np.percentile(col, [25, 75])
            lines.append(
                f"| {name} | {col.mean():.3f} | {np.median(col):.3f} | {col.std():.3f} "
                f"| {q3 - q1:.3f} | {col.min():.3f} | {col.max():.3f} |"
            )
        # flag outlier segments on active features
        flags = []
        for name, idx in _ACTIVE.items():
            col = vecs[:, idx].mean(axis=1) if isinstance(idx, slice) else vecs[:, idx]
            q1, q3 = np.percentile(col, [25, 75])
            iqr = q3 - q1
            lo, hi = q1 - _OUTLIER_IQR_K * iqr, q3 + _OUTLIER_IQR_K * iqr
            mu, sd = col.mean(), col.std()
            for j, val in enumerate(col):
                if (iqr > 0 and (val < lo or val > hi)) or (sd > 0 and abs(val - mu) > _OUTLIER_STD_K * sd):
                    s = by_tone[tone][j][1]
                    flags.append(f"  - {name}={val:.3f}  {s['filename']}  "
                                 f"{s['start_ms']/1000:.0f}-{s['end_ms']/1000:.0f}s")
        if flags:
            lines.append("\n**Outlier segments:**")
            lines.extend(sorted(set(flags)))

    lines.append("\n## Near-silent segments (>10% frames below rms threshold)\n")
    if near_silent:
        for frac, s in sorted(near_silent, key=lambda x: x[0], reverse=True):
            lines.append(f"  - {frac*100:.0f}% silent  [{s['tone']}]  {s['filename']}  "
                         f"{s['start_ms']/1000:.0f}-{s['end_ms']/1000:.0f}s")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _condition_name(frame_stat, cross_stat, metric):
    return f"frame={frame_stat:<6} cross={cross_stat:<6} dist={metric}"


def run_grid(segments):
    results = []
    for frame_stat, cross_stat, metric in itertools.product(
        ["mean", "median"], ["mean", "median"], ["l2", "l1"]
    ):
        m = run_condition(segments, frame_stat, cross_stat, metric)
        sep = archetype_separation(segments, frame_stat, cross_stat, metric)
        results.append({
            "frame_stat": frame_stat, "cross_stat": cross_stat, "metric": metric,
            "name": _condition_name(frame_stat, cross_stat, metric),
            "metrics": m, "edge_crunch_dist": sep["edge-crunch"], "separation": sep,
        })
    return results


def _format_report(results, segments):
    counts = defaultdict(int)
    for s in segments:
        counts[s["tone"]] += 1
    n_tracks = len({s["hash"] for s in segments})

    out = ["# EXP-001 Results — Mean vs Median Calibration\n"]
    out.append(f"Data: {len(segments)} manually-corrected segments across {n_tracks} tracks.")
    out.append("Per-tone counts: " + ", ".join(f"{t}={counts[t]}" for t in sorted(counts)) + "\n")
    out.append("Evaluation: leave-one-out cross-validation. "
               "`frame`=within-segment frame reduction, `cross`=cross-segment archetype, "
               "`dist`=distance metric.\n")

    out.append("## Condition results (sorted by macro-F1)\n")
    out.append("| condition | acc(all) | acc(no-other) | macro-F1 | edge/crunch conf | edge-crunch dist | conf ok/err |")
    out.append("|---|---|---|---|---|---|---|")
    for r in sorted(results, key=lambda r: r["metrics"]["macro_f1"], reverse=True):
        m = r["metrics"]
        out.append(
            f"| {r['name']} | {m['accuracy_all']:.3f} | {m['accuracy_no_other']:.3f} "
            f"| {m['macro_f1']:.3f} | {m['edge_crunch_confusion']:.3f} "
            f"| {r['edge_crunch_dist']:.3f} | {m['conf_correct']:.2f}/{m['conf_wrong']:.2f} |"
        )

    best = max(results, key=lambda r: r["metrics"]["macro_f1"])
    out.append(f"\n**Top by macro-F1:** `{best['name']}`\n")

    out.append("\n## Per-tone F1 (best condition vs production baseline)\n")
    baseline = next(r for r in results
                    if r["frame_stat"] == "mean" and r["cross_stat"] == "mean" and r["metric"] == "l2")
    out.append(f"Baseline = `{baseline['name']}` (current calibration behaviour).\n")
    out.append("| tone | support | baseline F1 | best F1 |")
    out.append("|---|---|---|---|")
    for t in TONE_LABELS:
        bs = baseline["metrics"]["per_tone"][t]
        be = best["metrics"]["per_tone"][t]
        if bs[3] == 0:
            continue
        out.append(f"| {t} | {bs[3]} | {bs[2]:.3f} | {be[2]:.3f} |")

    out.append("\n## Confusion matrix — best condition\n")
    cm = best["metrics"]["confusion"]
    labels = list(TONE_LABELS)
    out.append("| true \\ pred | " + " | ".join(labels) + " |")
    out.append("|" + "---|" * (len(labels) + 1))
    for t in labels:
        row = " | ".join(str(cm[t][p]) for p in labels)
        out.append(f"| {t} | {row} |")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="EXP-001 mean-vs-median calibration harness.")
    parser.add_argument("--db", default="library.db")
    parser.add_argument("--stems-dir", default="stems")
    parser.add_argument("--music-dir", default="music")
    parser.add_argument("--out", default="results/exp-001")
    parser.add_argument("--diagnose", action="store_true", help="Run outlier diagnostic only")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    db = init_db(args.db)
    print("Loading and extracting features for labeled segments...")
    segments = load_segment_frames(
        db, Path(args.stems_dir), Path(args.music_dir) if args.music_dir else None
    )
    if not segments:
        print("No labeled segments found.")
        return
    print(f"Loaded {len(segments)} segments.\n")

    diag = diagnose(segments)
    (out_dir / "diagnostic.md").write_text(diag, encoding="utf-8")
    print(diag)
    if args.diagnose:
        print(f"\nDiagnostic written to {out_dir / 'diagnostic.md'}")
        return

    print("\nRunning 8-condition LOOCV grid...")
    results = run_grid(segments)
    report = _format_report(results, segments)
    (out_dir / "summary.md").write_text(report, encoding="utf-8")

    serializable = [
        {k: v for k, v in r.items() if k != "metrics"}
        | {"metrics": {mk: mv for mk, mv in r["metrics"].items()
                       if mk not in ("confusion", "per_tone")}}
        for r in results
    ]
    (out_dir / "conditions.json").write_text(json.dumps(serializable, indent=2), encoding="utf-8")

    print("\n" + report)
    print(f"\nWritten: {out_dir / 'summary.md'}, {out_dir / 'diagnostic.md'}, "
          f"{out_dir / 'conditions.json'}")


if __name__ == "__main__":
    main()
