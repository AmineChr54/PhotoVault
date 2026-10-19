"""Worker CLI (run with data\venv python):  python -m photovault.ai.worker index [--limit N] [--cpu]"""
import argparse
import sys


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
    a = ap.parse_args(argv)
    if a.cmd == "retag":
        from . import autotag, indexer, store
        from .models import SigLIP
        conn = store.connect()
        vid, fit = store.model_choice(conn, indexer.selected_model())   # match the stored embeddings
        print(autotag.retag_all(conn, SigLIP(False, vid, fit).encode_text), "tags written")
        return
    if a.cmd == "index":
        from . import indexer
        indexer.run(limit=a.limit, use_faces=not a.no_faces, prefer_gpu=not a.cpu, workers=a.workers,
                    sample_every=a.sample_every)


if __name__ == "__main__":
    sys.exit(main())
