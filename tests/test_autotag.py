"""Auto 'thing' tags with fake embeddings: no models, network or real library."""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xxhash")
pytest.importorskip("PIL")

from photovault.ai import autotag, store, vocab  # noqa: E402

DIM = 64
calls = []


def _vec(word):
    r = np.random.default_rng(sum(map(ord, word)) * 7919)
    v = r.normal(size=DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def fake_text(texts):
    calls.append(len(texts))
    return np.stack([_vec(t.replace("a photo of ", "")) for t in texts])


@pytest.fixture
def conn(tmp_path):
    calls.clear()
    c = store.connect(tmp_path / "ai.db")
    yield c
    c.close()


def names(conn, h):
    return {r["name"]: r for r in store.tags_for_photo(conn, h)}


def test_vocab_shape():
    labels = [v[0] for v in vocab.VOCAB]
    assert 100 <= len(labels) <= 200 and len(set(labels)) == len(labels)


def test_tags_matching_labels_and_cap(conn):
    autotag.tag_embeddings(conn, [("h1", _vec("beach"))], fake_text)
    t = names(conn, "h1")
    assert "beach" in t and t["beach"]["source"] == "auto" and t["beach"]["kind"] == "thing"
    assert t["beach"]["score"] > 0.5
    assert len(t) <= vocab.TOP_K


def test_unrelated_embedding_gets_no_tags(conn, monkeypatch):
    monkeypatch.setattr(vocab, "DEFAULT_THRESHOLD", 0.5)  # random 64-d vectors are noisy
    autotag.tag_embeddings(conn, [("h1", np.ones(DIM, np.float32))], fake_text)
    assert names(conn, "h1") == {}


def test_top_k_cap(conn, monkeypatch):
    monkeypatch.setattr(vocab, "DEFAULT_THRESHOLD", -1.0)
    monkeypatch.setattr(vocab, "VOCAB", [(n, s, -1.0) for n, s, _t in vocab.VOCAB])
    autotag.tag_embeddings(conn, [("h1", _vec("x"))], fake_text, top_k=3)
    assert len(names(conn, "h1")) == 3


def test_text_vectors_cached_once(conn):
    autotag.tag_embeddings(conn, [("h1", _vec("dog"))], fake_text)
    autotag.tag_embeddings(conn, [("h2", _vec("cat"))], fake_text)
    assert len(calls) == 1
    autotag.tag_embeddings(conn, [("h3", _vec("dog"))], None)  # warm cache needs no encoder
    assert "dog" in names(conn, "h3")


def test_manual_and_rejected_never_overwritten(conn):
    autotag.tag_embeddings(conn, [("h1", _vec("dog"))], fake_text)
    dog = store.get_or_create_tag(conn, "dog", "thing")
    store.reject_photo_tag(conn, "h1", dog)
    cat = store.get_or_create_tag(conn, "cat", "thing")
    store.add_photo_tag(conn, "h1", cat, "manual")
    autotag.tag_embeddings(conn, [("h1", _vec("dog"))], fake_text)
    t = names(conn, "h1")
    assert "dog" not in t  # rejection survives re-tagging
    assert t["cat"]["source"] == "manual"  # manual survives although the embedding does not match


def test_retag_drops_stale_auto(conn):
    conn.execute("INSERT INTO embedding(hash,vec) VALUES (?,?)", ("h1", store.vec_to_blob(_vec("beach"))))
    conn.commit()
    autotag.retag_all(conn, fake_text)
    assert "beach" in names(conn, "h1")
    conn.execute("UPDATE embedding SET vec=? WHERE hash='h1'", (store.vec_to_blob(_vec("dog")),))
    conn.commit()
    autotag.retag_all(conn, fake_text)
    t = names(conn, "h1")
    assert "beach" not in t and "dog" in t
