"""Semantic search over ai.db: SigLIP text->image similarity plus person / year filters."""
import numpy as np

from . import store


class Index:
    """In-memory copy of the embeddings; call reload() after indexing."""

    def __init__(self, conn):
        self.conn = conn
        self.reload()

    def reload(self):
        self.hashes, self.mat = store.load_embeddings(self.conn)
        self.pos = {h: i for i, h in enumerate(self.hashes)}
        info = {r["hash"]: r for r in self.conn.execute("SELECT hash,path,date_taken FROM photo WHERE status='ok'")}
        self.path = [info[h]["path"] if h in info else None for h in self.hashes]
        self.date = [(info[h]["date_taken"] or "") if h in info else "" for h in self.hashes]
        self.tags = store.tag_hash_map(self.conn)

    def person_hashes(self, names):
        """Hashes of photos containing ALL the given people."""
        sets = []
        for nm in names:
            rows = self.conn.execute(
                "SELECT DISTINCT f.hash FROM face f JOIN person p ON p.id=f.person_id "
                "WHERE f.rejected=0 AND lower(p.name)=lower(?)", (nm,)).fetchall()
            sets.append({r["hash"] for r in rows})
        return set.intersection(*sets) if sets else set()

    def tag_hashes(self, names):
        """Hashes of photos carrying ALL the given tags (case-insensitive)."""
        sets = [{h for h, ts in self.tags.items() if nm.lower() in ts} for nm in names]
        return set.intersection(*sets) if sets else set()

    def search(self, text_vec=None, people=(), year=None, limit=200, tags=()):
        """Rank photos. text_vec: normalised query embedding or None (then newest first)."""
        idx = np.arange(len(self.hashes))
        if people:
            keep = self.person_hashes(people)
            idx = np.array([i for i in idx if self.hashes[i] in keep], dtype=int)
        if tags:
            keep = self.tag_hashes(tags)
            idx = np.array([i for i in idx if self.hashes[i] in keep], dtype=int)
        if year:
            idx = np.array([i for i in idx if self.date[i].startswith(str(year))], dtype=int)
        if idx.size == 0:
            return []
        if text_vec is None:
            order = sorted(idx, key=lambda i: self.date[i], reverse=True)[:limit]
            return [(self.hashes[i], self.path[i], 0.0) for i in order]
        sims = self.mat[idx] @ text_vec
        top = np.argsort(-sims)[:limit]
        return [(self.hashes[idx[t]], self.path[idx[t]], float(sims[t])) for t in top]
