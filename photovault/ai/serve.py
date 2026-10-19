"""Persistent worker: JSON lines on stdin -> JSON lines on stdout. Keeps SigLIP and the embedding matrix
loaded so searches take milliseconds. Commands: search, recluster, events, assign, stats, reload, index."""
import json
import sys
import threading
import time


def main():
    from . import events, indexer, people, search, store
    from .models import SigLIP
    conn = store.connect()
    state = {"ix": search.Index(conn), "sig": None, "index_thread": None, "cancel": threading.Event()}
    out_lock = threading.Lock()

    def send(obj):
        with out_lock:
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()

    def sig():
        if state["sig"] is None:
            # Queries must use the model that produced the stored embeddings, not whatever is selected now.
            vid, fit = store.model_choice(conn, indexer.selected_model())
            state["sig"] = SigLIP(True, vid, fit)
        return state["sig"]

    def do_search(req):
        people_names, text = people.parse_query(conn, req.get("q", ""))
        if req.get("people"):
            people_names = list(dict.fromkeys(people_names + list(req["people"])))
        tag_names, text = store.match_tag_names(conn, text)
        tag_names = list(dict.fromkeys(tag_names + list(req.get("tags") or [])))
        vec = sig().encode_text([text])[0] if text else None
        t = time.time()
        res = state["ix"].search(vec, people=people_names, year=req.get("year"), tags=tag_names, limit=req.get("limit", 300))
        return {"people": people_names, "tags": tag_names, "text": text, "ms": round((time.time() - t) * 1000),
                "results": [{"hash": h, "path": p, "score": s} for h, p, s in res]}

    def run_index(req):
        import io
        real = sys.stdout
        try:
            indexer.run(limit=req.get("limit"), use_faces=True, prefer_gpu=True, cancel=state["cancel"],
                        sample_every=req.get("sample_every"))
        except Exception as e:
            send({"event": "index_error", "error": f"{type(e).__name__}: {e}"})
        finally:
            sys.stdout = real
            state["sig"] = None   # the index may now come from a different model
            state["ix"].reload()
            send({"event": "index_finished"})

    handlers = {
        "search": do_search,
        "stats": lambda r: store.stats(conn),
        "reload": lambda r: (state["ix"].reload(), {"photos": len(state["ix"].hashes)})[1],
        "recluster": lambda r: {"clusters": people.recluster(conn)},
        "events": lambda r: {"events": events.recompute(
            conn, r.get("gap_hours", events.GAP_HOURS), r.get("split_km", events.SPLIT_KM))},
        "assign": lambda r: {"assigned": people.assign_known(conn, r.get("threshold", people.ASSIGN_THRESHOLD))},
        "cancel": lambda r: (state["cancel"].set(), {})[1],
    }
    send({"event": "ready", "photos": len(state["ix"].hashes)})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            cmd = req.get("cmd")
            if cmd == "index":
                if state["index_thread"] and state["index_thread"].is_alive():
                    send({"id": req.get("id"), "error": "indexing already running"})
                    continue
                state["cancel"].clear()
                state["index_thread"] = threading.Thread(target=run_index, args=(req,), daemon=True)
                state["index_thread"].start()
                send({"id": req.get("id"), "ok": True})
                continue
            res = handlers[cmd](req)
            send({"id": req.get("id"), "ok": True, **res})
        except Exception as e:
            send({"id": (req or {}).get("id") if isinstance(req, dict) else None,
                  "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
