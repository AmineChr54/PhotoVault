"""Quality benchmark and per-label tag calibration for the AI index (issue #10).

A small hand-labelled JSON file (see docs/ai_benchmark_labels.example.json) describes
  - "search": queries with the photos that should come back,
  - "tags":   photos with labels that are present / absent,
  - "faces":  pairs of photos that do / do not show the same person.
Photos are referenced by content hash or by path (absolute, or relative to the optional "root").

`run_benchmark` reports recall@k, tag precision/recall and face-pair accuracy against the stored index.
`calibrate` picks one cosine threshold per vocabulary label from the tag checks and saves it in ai.db
(autotag.save_calibrated_thresholds), replacing the single global 0.10 guess for labels with enough data.

Everything takes an `encode_text(list[str]) -> array` callable, so tests use fake embeddings and no model.
"""
import json
import os

import numpy as np

from . import autotag, search, store, vocab

DEFAULT_KS = (1, 5, 10)
MIN_POSITIVES = 2
MIN_NEGATIVES = 2
F_BETA = 0.5  # < 1 weights precision over recall: a wrong tag is worse than a missing one


# ---------- labels file ----------
def load_labels(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return validate_labels(data)


def validate_labels(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("labels file must be a JSON object")
    out = {"root": data.get("root") or "", "search": [], "tags": [], "faces": []}
    for i, q in enumerate(data.get("search") or []):
        if not isinstance(q, dict) or not str(q.get("query", "")).strip() or not q.get("expected"):
            raise ValueError(f"search[{i}] needs a 'query' and a non-empty 'expected' list")
        out["search"].append({"query": str(q["query"]).strip(), "expected": [str(x) for x in q["expected"]]})
    for i, t in enumerate(data.get("tags") or []):
        if not isinstance(t, dict) or not t.get("photo"):
            raise ValueError(f"tags[{i}] needs a 'photo'")
        present, absent = [str(x).lower() for x in t.get("present") or []], [str(x).lower() for x in t.get("absent") or []]
        if not present and not absent:
            raise ValueError(f"tags[{i}] needs 'present' and/or 'absent' labels")
        if set(present) & set(absent):
            raise ValueError(f"tags[{i}] lists a label as both present and absent")
        out["tags"].append({"photo": str(t["photo"]), "present": present, "absent": absent})
    for i, fc in enumerate(data.get("faces") or []):
        if not isinstance(fc, dict) or len(fc.get("photos") or []) != 2 or not isinstance(fc.get("same"), bool):
            raise ValueError(f"faces[{i}] needs 'photos' (two photos) and a boolean 'same'")
        out["faces"].append({"photos": [str(x) for x in fc["photos"]], "same": fc["same"]})
    return out


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


class Resolver:
    """Maps a hash or a path from the labels file to a hash in ai.db (None if the photo is not indexed)."""

    def __init__(self, conn, root=""):
        self.root = root
        self.by_hash = {r["hash"] for r in conn.execute("SELECT hash FROM photo WHERE status='ok'")}
        self.by_path = {}
        for r in conn.execute("SELECT hash, path FROM photo WHERE status='ok'"):
            self.by_path[_norm(r["path"])] = r["hash"]
        self.unresolved = []

    def __call__(self, ref):
        if ref in self.by_hash:
            return ref
        p = ref if os.path.isabs(ref) or not self.root else os.path.join(self.root, ref)
        h = self.by_path.get(_norm(p))
        if h is None and ref not in self.unresolved:
            self.unresolved.append(ref)
        return h


# ---------- search: recall@k ----------
def run_search(conn, labels, encode_text, ks=DEFAULT_KS, resolver=None) -> dict:
    resolver = resolver or Resolver(conn, labels.get("root", ""))
    ks = sorted(set(int(k) for k in ks))
    items = []
    for q in labels["search"]:
        exp = [h for h in (resolver(x) for x in q["expected"]) if h]
        if exp:
            items.append((q["query"], set(exp)))
    ix = search.Index(conn)
    vecs = np.asarray(encode_text([q for q, _ in items]), np.float32) if items else np.zeros((0, 1), np.float32)
    per_query = []
    for (query, exp), v in zip(items, vecs):
        v = v / (np.linalg.norm(v) + 1e-9)
        ranked = [h for h, _p, _s in ix.search(v, limit=max(ks))]
        row = {"query": query, "expected": len(exp)}
        for k in ks:
            found = len(exp & set(ranked[:k]))
            row[f"recall@{k}"] = found / len(exp)
            row[f"hit@{k}"] = 1.0 if found else 0.0
        per_query.append(row)
    n = len(per_query)
    res = {"queries": n}
    for k in ks:
        res[f"recall@{k}"] = sum(r[f"recall@{k}"] for r in per_query) / n if n else None
        res[f"hit@{k}"] = sum(r[f"hit@{k}"] for r in per_query) / n if n else None
    res["per_query"] = per_query
    return res


# ---------- tags: precision / recall ----------
def _tag_samples(conn, labels, encode_text, resolver):
    """[(hash, cosine score per vocab label, present set, absent set)] plus (vocab labels, matrix, thresholds)."""
    vlabels, thr, mat = autotag.label_vectors(conn, encode_text)
    known = set(vlabels)
    emb = dict(zip(*store.load_embeddings(conn)))
    samples, unknown = [], set()
    for t in labels["tags"]:
        h = resolver(t["photo"])
        if h is None or h not in emb:
            continue
        present, absent = set(t["present"]), set(t["absent"])
        unknown |= (present | absent) - known
        scores = mat @ emb[h]
        samples.append((h, scores, present & known, absent & known))
    return vlabels, thr, samples, sorted(unknown)


def run_tags(conn, labels, encode_text, resolver=None, top_k=None, thresholds=None) -> dict:
    """Precision / recall of the auto tagger on the judged labels only (labels not listed as present or absent
    for a photo are unknown, so predictions of them are counted separately and not held against it)."""
    resolver = resolver or Resolver(conn, labels.get("root", ""))
    vlabels, thr, samples, unknown = _tag_samples(conn, labels, encode_text, resolver)
    if thresholds is not None:
        thr = np.array([thresholds.get(n, t) for n, t in zip(vlabels, thr)], np.float32)
    idx = {n: i for i, n in enumerate(vlabels)}
    tp = fp = fn = extra = 0
    per = {}
    for _h, scores, present, absent in samples:
        order = [int(i) for i in np.argsort(-scores) if scores[i] >= thr[i]][: top_k or vocab.TOP_K]
        predicted = {vlabels[i] for i in order}
        for name in present | absent:
            d = per.setdefault(name, {"tp": 0, "fp": 0, "fn": 0})
            hit = name in predicted
            if name in present:
                d["tp" if hit else "fn"] += 1
            elif hit:
                d["fp"] += 1
        tp += len(predicted & present)
        fp += len(predicted & absent)
        fn += len(present - predicted)
        extra += len(predicted - present - absent)
    res = {
        "photos": len(samples), "tp": tp, "fp": fp, "fn": fn, "unjudged_predictions": extra,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "unknown_labels": unknown,
        "per_label": {k: {**v, "threshold": float(thr[idx[k]])} for k, v in sorted(per.items())},
    }
    return res


# ---------- faces: same-person pair accuracy ----------
def run_faces(conn, labels, resolver=None) -> dict:
    """A pair is predicted 'same person' when the two photos share a face cluster. Meant for photos with one
    face each; photos without a clustered face count as 'different'."""
    resolver = resolver or Resolver(conn, labels.get("root", ""))

    def clusters(h):
        return {r[0] for r in conn.execute(
            "SELECT DISTINCT cluster FROM face WHERE hash=? AND rejected=0 AND cluster IS NOT NULL AND cluster>=0", (h,))}

    ok = n = 0
    for fc in labels["faces"]:
        a, b = (resolver(x) for x in fc["photos"])
        if a is None or b is None:
            continue
        n += 1
        ok += (bool(clusters(a) & clusters(b))) == fc["same"]
    return {"pairs": n, "correct": ok, "accuracy": ok / n if n else None}


# ---------- calibration ----------
def best_threshold(pos, neg, beta=F_BETA):
    """Cosine threshold that maximises F-beta for tagging a label, or None if no threshold gets any positive
    right. Ties go to the higher (more conservative) threshold. Candidates sit midway between neighbouring scores."""
    pos, neg = sorted(float(x) for x in pos), sorted(float(x) for x in neg)
    allv = sorted(set(pos + neg))
    cands = [allv[0] - 1e-3] + [(a + b) / 2 for a, b in zip(allv, allv[1:])] + [allv[-1] + 1e-3]
    b2 = beta * beta
    best, best_f = None, 0.0
    for t in cands:
        tp = sum(1 for x in pos if x >= t)
        fp = sum(1 for x in neg if x >= t)
        fn = len(pos) - tp
        if tp == 0:
            continue
        f = (1 + b2) * tp / ((1 + b2) * tp + b2 * fn + fp)
        if f >= best_f - 1e-12:
            best, best_f = t, max(f, best_f)
    return best


def calibrate(conn, labels, encode_text, resolver=None, min_pos=MIN_POSITIVES, min_neg=MIN_NEGATIVES) -> dict:
    """Compute per-label thresholds from the tag checks (nothing is saved). Labels with fewer than min_pos
    present or min_neg absent examples keep their current threshold. Returns {"thresholds", "skipped", ...}."""
    resolver = resolver or Resolver(conn, labels.get("root", ""))
    vlabels, thr, samples, unknown = _tag_samples(conn, labels, encode_text, resolver)
    out, skipped = {}, {}
    for i, name in enumerate(vlabels):
        pos = [s[1][i] for s in samples if name in s[2]]
        neg = [s[1][i] for s in samples if name in s[3]]
        if not pos and not neg:
            continue
        if len(pos) < min_pos or len(neg) < min_neg:
            skipped[name] = {"positives": len(pos), "negatives": len(neg)}
            continue
        t = best_threshold(pos, neg)
        if t is None:
            skipped[name] = {"positives": len(pos), "negatives": len(neg), "reason": "no usable threshold"}
        else:
            out[name] = round(t, 5)
    return {"thresholds": out, "skipped": skipped, "unknown_labels": unknown, "photos": len(samples)}


def calibrate_and_save(conn, labels, encode_text, merge=True, **kw) -> dict:
    """Calibrate and store. merge=True keeps previously calibrated labels not covered by this run."""
    res = calibrate(conn, labels, encode_text, **kw)
    new = dict(autotag.calibrated_thresholds(conn)) if merge else {}
    new.update(res["thresholds"])
    autotag.save_calibrated_thresholds(conn, new)
    res["saved"] = len(new)
    return res


# ---------- whole benchmark ----------
def run_benchmark(conn, labels, encode_text, ks=DEFAULT_KS) -> dict:
    resolver = Resolver(conn, labels.get("root", ""))
    res = {}
    if labels["search"]:
        res["search"] = run_search(conn, labels, encode_text, ks, resolver)
    if labels["tags"]:
        res["tags"] = run_tags(conn, labels, encode_text, resolver)
    if labels["faces"]:
        res["faces"] = run_faces(conn, labels, resolver)
    res["unresolved"] = list(resolver.unresolved)
    res["calibrated_labels"] = len(autotag.calibrated_thresholds(conn))
    return res


def _pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def format_report(res) -> str:
    lines = []
    s = res.get("search")
    if s:
        lines.append(f"Search ({s['queries']} queries)")
        for k in sorted(int(key.split('@')[1]) for key in s if key.startswith("recall@")):
            lines.append(f"  recall@{k}: {_pct(s[f'recall@{k}'])}   hit@{k}: {_pct(s[f'hit@{k}'])}")
        worst = sorted(s["per_query"], key=lambda r: min(v for kk, v in r.items() if kk.startswith("recall@")))[:5]
        for r in worst:
            lines.append(f"    weak: {r['query']!r} (expected {r['expected']})")
    t = res.get("tags")
    if t:
        lines.append(f"Tags ({t['photos']} photos, calibrated labels: {res.get('calibrated_labels', 0)})")
        lines.append(f"  precision: {_pct(t['precision'])}   recall: {_pct(t['recall'])}   "
                     f"(tp {t['tp']}, fp {t['fp']}, fn {t['fn']}, unjudged predictions {t['unjudged_predictions']})")
        bad = [(k, v) for k, v in t["per_label"].items() if v["fp"] or v["fn"]]
        for k, v in sorted(bad, key=lambda kv: -(kv[1]["fp"] + kv[1]["fn"]))[:10]:
            lines.append(f"    {k}: fp {v['fp']}, fn {v['fn']} (threshold {v['threshold']:.3f})")
        if t["unknown_labels"]:
            lines.append("  labels not in vocabulary (ignored): " + ", ".join(t["unknown_labels"]))
    f = res.get("faces")
    if f:
        lines.append(f"Faces ({f['pairs']} pairs): accuracy {_pct(f['accuracy'])}")
    if res.get("unresolved"):
        lines.append(f"{len(res['unresolved'])} photo reference(s) not found in the index, e.g. {res['unresolved'][0]}")
    return "\n".join(lines) if lines else "Nothing to evaluate: the labels file has no checks."


def format_calibration(res) -> str:
    lines = [f"Calibrated {len(res['thresholds'])} label(s) from {res['photos']} labelled photo(s); "
             f"{len(res['skipped'])} skipped (need >= {MIN_POSITIVES} present and >= {MIN_NEGATIVES} absent examples)."]
    for k, v in sorted(res["thresholds"].items()):
        lines.append(f"  {k}: {v:.3f}")
    if res["unknown_labels"]:
        lines.append("labels not in vocabulary (ignored): " + ", ".join(res["unknown_labels"]))
    return "\n".join(lines)
