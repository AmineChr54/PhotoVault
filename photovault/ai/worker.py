"""Worker CLI (run with data\venv python):  python -m photovault.ai.worker index [--limit N] [--cpu]"""
import argparse
import sys


def _quality(a):
    import json

    from . import autotag, benchmark, store
    from .models import SigLIP
    conn = store.connect()
    if a.cmd == "calibrate" and a.reset:
        autotag.save_calibrated_thresholds(conn, {})
        print("calibrated thresholds cleared")
        if a.retag:
            print(autotag.retag_all(conn, SigLIP(False).encode_text), "tags written")
        return
    if not a.labels:
        print("a labels file is required", file=sys.stderr)
        return 2
    labels = benchmark.load_labels(a.labels)
    enc = SigLIP(False).encode_text
    if a.cmd == "benchmark":
        res = benchmark.run_benchmark(conn, labels, enc, [int(k) for k in a.k.split(",") if k.strip()])
        print(json.dumps(res, indent=2) if a.json else benchmark.format_report(res))
        return
    res = benchmark.calibrate_and_save(conn, labels, enc, merge=not a.replace)
    print(benchmark.format_calibration(res))
    if a.retag:
        print(autotag.retag_all(conn, enc), "tags written")
    else:
        print("Run `retag` (or calibrate --retag) to apply the new thresholds to existing photos.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="photovault.ai.worker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ix = sub.add_parser("index")
    ix.add_argument("--limit", type=int)
    ix.add_argument("--sample-every", type=int, help="process every Nth new photo (for testing)")
    ix.add_argument("--no-faces", action="store_true")
    ix.add_argument("--cpu", action="store_true")
    ix.add_argument("--workers", type=int, default=4)
    sub.add_parser("retag", help="re-score auto 'thing' tags for all indexed photos")
    bm = sub.add_parser("benchmark", help="measure search recall@k, tag precision and face pairs against a labels file")
    bm.add_argument("labels")
    bm.add_argument("--k", default="1,5,10", help="comma separated k values for recall@k")
    bm.add_argument("--json", action="store_true", help="print the raw result as JSON")
    ca = sub.add_parser("calibrate", help="learn per-label tag thresholds from the tag checks in a labels file")
    ca.add_argument("labels", nargs="?")
    ca.add_argument("--replace", action="store_true", help="drop earlier calibrated labels not in this file")
    ca.add_argument("--reset", action="store_true", help="forget all calibrated thresholds (back to vocab defaults)")
    ca.add_argument("--retag", action="store_true", help="re-score all auto tags afterwards")
    a = ap.parse_args(argv)
    if a.cmd in ("benchmark", "calibrate"):
        return _quality(a)
    if a.cmd == "retag":
        from . import autotag, store
        from .models import SigLIP
        print(autotag.retag_all(store.connect(), SigLIP(False).encode_text), "tags written")
        return
    if a.cmd == "index":
        from . import indexer
        indexer.run(limit=a.limit, use_faces=not a.no_faces, prefer_gpu=not a.cpu, workers=a.workers,
                    sample_every=a.sample_every)


if __name__ == "__main__":
    sys.exit(main())
