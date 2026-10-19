"""People: cluster unnamed faces, name clusters, assign known people. SQL-only helpers are numpy-free
(used by the GUI); clustering functions import numpy/sklearn lazily (worker venv)."""
import re

from . import store

ASSIGN_THRESHOLD = 0.42   # cosine similarity needed to auto-assign a face to a named person
STOP = {"and", "with", "und", "et", "mit", "avec", "&", ","}


# ---------- SQL only ----------
def person_id(conn, name: str) -> int:
    name = name.strip()
    row = conn.execute("SELECT id FROM person WHERE lower(name)=lower(?)", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO person(name) VALUES (?)", (name,)).lastrowid


def name_cluster(conn, cluster: int, name: str) -> int:
    pid = person_id(conn, name)
    n = conn.execute("UPDATE face SET person_id=?, manual=1 WHERE cluster=? AND person_id IS NULL AND rejected=0",
                     (pid, cluster)).rowcount
    conn.commit()
    return n


def reject_face(conn, face_id: int) -> None:
    conn.execute("UPDATE face SET rejected=1, person_id=NULL WHERE id=?", (face_id,))
    conn.commit()


def reject_cluster(conn, cluster: int) -> None:
    conn.execute("UPDATE face SET rejected=1 WHERE cluster=? AND person_id IS NULL", (cluster,))
    conn.commit()


def assign_face(conn, face_id: int, name: str) -> None:
    pid = person_id(conn, name)
    conn.execute("UPDATE face SET person_id=?, manual=1, rejected=0 WHERE id=?", (pid, face_id))
    conn.commit()


def rename_person(conn, pid: int, name: str) -> None:
    conn.execute("UPDATE person SET name=? WHERE id=?", (name.strip(), pid))
    conn.commit()


def merge_persons(conn, src: int, dst: int) -> None:
    conn.execute("UPDATE face SET person_id=? WHERE person_id=?", (dst, src))
    conn.execute("DELETE FROM person WHERE id=?", (src,))
    conn.commit()


def clusters(conn, limit=60, min_size=2):
    """Unnamed clusters, biggest first, each with its best sample faces."""
    rows = conn.execute("SELECT cluster, COUNT(*) n FROM face WHERE cluster>=0 AND person_id IS NULL AND rejected=0 "
                        "GROUP BY cluster HAVING n>=? ORDER BY n DESC LIMIT ?", (min_size, limit)).fetchall()
    out = []
    for r in rows:
        faces = conn.execute("SELECT id FROM face WHERE cluster=? AND person_id IS NULL AND rejected=0 "
                             "ORDER BY score DESC LIMIT 8", (r["cluster"],)).fetchall()
        out.append({"cluster": r["cluster"], "size": r["n"], "faces": [f["id"] for f in faces]})
    return out


def persons(conn):
    return [dict(r) for r in conn.execute(
        "SELECT p.id, p.name, COUNT(f.id) faces, COUNT(DISTINCT f.hash) photos FROM person p "
        "LEFT JOIN face f ON f.person_id=p.id AND f.rejected=0 GROUP BY p.id ORDER BY photos DESC")]


def person_faces(conn, pid: int, limit=8):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM face WHERE person_id=? AND rejected=0 ORDER BY manual DESC, score DESC LIMIT ?", (pid, limit))]


def parse_query(conn, query: str):
    """Split 'Mom and Nils at the beach' into (['Mom','Nils'], 'at the beach')."""
    names = [r["name"] for r in conn.execute("SELECT name FROM person")]
    found = []
    rest = query
    for nm in sorted(names, key=len, reverse=True):
        pat = re.compile(r"(?<!\w)" + re.escape(nm) + r"(?!\w)", re.I)
        if pat.search(rest):
            found.append(nm)
            rest = pat.sub(" ", rest)
    words = [w for w in re.split(r"\s+", rest) if w and w.lower() not in STOP]
    return found, " ".join(words).strip()


# ---------- numpy / sklearn (worker) ----------
def _load_faces(conn, where):
    import numpy as np
    rows = conn.execute(f"SELECT id, emb FROM face WHERE rejected=0 AND {where}").fetchall()
    if not rows:
        return [], np.zeros((0, 512), np.float32)
    return [r["id"] for r in rows], np.stack([store.blob_to_vec(r["emb"], np.float32) for r in rows])


def recluster(conn, min_cluster_size=4):
    """Cluster all unnamed faces with HDBSCAN. Named faces are untouched, so names survive re-runs."""
    import numpy as np
    from sklearn.cluster import HDBSCAN
    ids, x = _load_faces(conn, "person_id IS NULL")
    if len(ids) < min_cluster_size:
        return 0
    x = x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)
    labels = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=2, metric="euclidean").fit_predict(x)
    base = (conn.execute("SELECT COALESCE(MAX(cluster),0) FROM face").fetchone()[0] or 0) + 1
    conn.executemany("UPDATE face SET cluster=? WHERE id=?",
                     [(int(l) + base if l >= 0 else -1, int(i)) for i, l in zip(ids, labels)])
    conn.commit()
    return int(labels.max()) + 1 if labels.size else 0


def assign_known(conn, threshold=ASSIGN_THRESHOLD):
    """Give unnamed faces to the closest named person (cosine >= threshold, vote of the 5 nearest named faces)."""
    import numpy as np
    kn_ids, kn = _load_faces(conn, "person_id IS NOT NULL")
    if not kn_ids:
        return 0
    owner = {r["id"]: r["person_id"] for r in conn.execute(
        "SELECT id, person_id FROM face WHERE person_id IS NOT NULL AND rejected=0")}
    owners = np.array([owner[i] for i in kn_ids])
    un_ids, un = _load_faces(conn, "person_id IS NULL")
    if not un_ids:
        return 0
    sims = un @ kn.T
    changed = []
    for row, fid in zip(sims, un_ids):
        top = np.argsort(-row)[:5]
        votes = {}
        for t in top:
            if row[t] >= threshold:
                votes[int(owners[t])] = votes.get(int(owners[t]), 0) + float(row[t])
        if votes:
            changed.append((max(votes, key=votes.get), int(fid)))
    conn.executemany("UPDATE face SET person_id=?, manual=0 WHERE id=?", changed)
    conn.commit()
    return len(changed)
