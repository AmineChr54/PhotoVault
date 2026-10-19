"""Auto 'thing' tags: zero-shot scoring of stored SigLIP image embeddings against cached label text vectors.

Logic lives here (not in store.py). Auto rows go through store.add_photo_tag, which never overwrites a
manual tag or a manual rejection. Stale auto rows for a photo are replaced on re-tagging.
"""
import base64

import numpy as np

from . import store, vocab


def _meta_key():
    return f"tag_text_vecs_v{vocab.VOCAB_VERSION}"


def label_vectors(conn, encode_text=None):
    """(labels, thresholds, matrix[n, d]) with L2-normalised rows. Text vectors are computed once via
    encode_text(list[str]) -> array and cached in the meta table; with a warm cache no model is needed."""
    key = _meta_key()
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    labels = [v[0] for v in vocab.VOCAB]
    thr = np.array([vocab.DEFAULT_THRESHOLD if v[2] is None else v[2] for v in vocab.VOCAB], np.float32)
    if row:
        m = np.frombuffer(base64.b64decode(row["value"]), np.float32)
        if m.size % len(labels) == 0:
            return labels, thr, m.reshape(len(labels), -1)
    if encode_text is None:
        raise RuntimeError("label text vectors not cached and no text encoder given")
    prompts, owner = [], []
    for i, (name, syns, _t) in enumerate(vocab.VOCAB):
        for w in (name, *syns):
            prompts.append(vocab.PROMPT.format(w))
            owner.append(i)
    t = np.asarray(encode_text(prompts), np.float32)
    t = t / (np.linalg.norm(t, axis=1, keepdims=True) + 1e-9)
    owner = np.array(owner)
    m = np.stack([t[owner == i].mean(axis=0) for i in range(len(labels))])
    m = (m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)).astype(np.float32)
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
                 (key, base64.b64encode(m.tobytes()).decode("ascii")))
    conn.commit()
    return labels, thr, m


def pick(emb, matrix, thr, top_k=None):
    """Indices and cosine scores of labels above their threshold, best first, capped at top_k."""
    e = np.asarray(emb, np.float32)
    e = e / (np.linalg.norm(e) + 1e-9)
    s = matrix @ e
    idx = [int(i) for i in np.argsort(-s) if s[i] >= thr[i]][: top_k or vocab.TOP_K]
    return [(i, float(s[i])) for i in idx]


def tag_embeddings(conn, items, encode_text=None, top_k=None):
    """items: iterable of (hash, embedding). Writes source=auto thing tags; returns number written."""
    labels, thr, m = label_vectors(conn, encode_text)
    n = 0
    for h, e in items:
        chosen = pick(e, m, thr, top_k)
        keep = set()
        for i, sc in chosen:
            tid = store.get_or_create_tag(conn, labels[i], "thing")
            keep.add(tid)
            store.add_photo_tag(conn, h, tid, "auto", sc)
            n += 1
        # drop auto rows that no longer qualify (never touches manual / rejected rows)
        for r in conn.execute("SELECT pt.tag_id FROM photo_tag pt JOIN tag t ON t.id=pt.tag_id "
                              "WHERE pt.hash=? AND pt.source='auto' AND t.kind='thing'", (h,)).fetchall():
            if r["tag_id"] not in keep:
                conn.execute("DELETE FROM photo_tag WHERE hash=? AND tag_id=? AND source='auto'", (h, r["tag_id"]))
        conn.commit()
    return n


def retag_all(conn, encode_text=None, top_k=None):
    """Re-score every stored embedding (the `retag` worker command)."""
    items = ((r["hash"], store.blob_to_vec(r["vec"])) for r in conn.execute("SELECT hash, vec FROM embedding"))
    return tag_embeddings(conn, items, encode_text, top_k)
