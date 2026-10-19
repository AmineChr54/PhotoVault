"""AI quality benchmark + per-label calibration with fake embeddings: no models, network or real library."""
import json
import os
import pathlib
import sys
import types

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xxhash")
pytest.importorskip("PIL")

from photovault.ai import autotag, benchmark, store, vocab  # noqa: E402

DIM = 64


def _vec(word):
    r = np.random.default_rng(sum(map(ord, word)) * 7919)
    v = r.normal(size=DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def fake_text(texts):
    return np.stack([_vec(t.replace("a photo of ", "")) for t in texts])


def mix(word, other, w):
    v = _vec(word) + w * _vec(other)
    return v / np.linalg.norm(v)


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "ai.db")
    yield c
    c.close()


def add(conn, h, vec, path=None):
    conn.execute("INSERT INTO photo(hash,path) VALUES (?,?)", (h, path or f"/lib/{h}.jpg"))
    conn.execute("INSERT INTO embedding(hash,vec) VALUES (?,?)", (h, store.vec_to_blob(vec)))
    conn.commit()


def test_validate_labels_rejects_bad_input():
    with pytest.raises(ValueError):
        benchmark.validate_labels({"search": [{"query": "x"}]})
    with pytest.raises(ValueError):
        benchmark.validate_labels({"tags": [{"photo": "a", "present": ["dog"], "absent": ["dog"]}]})
    with pytest.raises(ValueError):
        benchmark.validate_labels({"faces": [{"photos": ["a"], "same": True}]})
    ok = benchmark.validate_labels({"tags": [{"photo": "a", "present": ["Dog"]}]})
    assert ok["tags"][0]["present"] == ["dog"]


def test_example_labels_file_is_valid():
    p = pathlib.Path(__file__).resolve().parent.parent / "docs" / "ai_benchmark_labels.example.json"
    lab = benchmark.load_labels(p)
    assert lab["search"] and lab["tags"] and lab["faces"]


def test_search_recall_at_k(conn):
    add(conn, "d1", _vec("dog on grass"))
    add(conn, "d2", mix("dog on grass", "x1", 0.3))
    add(conn, "c1", _vec("cake"))
    for i in range(6):
        add(conn, f"n{i}", _vec(f"noise{i}"))
    labels = benchmark.validate_labels({"search": [
        {"query": "dog on grass", "expected": ["d1", "d2"]},
        {"query": "cake", "expected": ["/lib/c1.jpg"]},          # by path
        {"query": "nothing", "expected": ["/lib/missing.jpg"]},   # unresolved -> skipped
    ]})
    res = benchmark.run_benchmark(conn, labels, lambda t: np.stack([_vec(x) for x in t]), ks=(1, 2))
    s = res["search"]
    assert s["queries"] == 2
    assert s["recall@2"] == 1.0 and s["hit@1"] == 1.0
    assert s["recall@1"] == pytest.approx(0.75)  # dog: 1 of 2 in top-1, cake: 1 of 1
    assert res["unresolved"] == ["/lib/missing.jpg"]
    assert "recall@5" not in s and "recall@2" in benchmark.format_report(res)


def test_root_relative_paths(conn):
    add(conn, "a", _vec("a"), path=os.path.join("/lib", "sub", "a.jpg"))
    r = benchmark.Resolver(conn, "/lib")
    assert r("sub/a.jpg") == "a" and r("nope.jpg") is None


def test_best_threshold_separates_and_prefers_conservative():
    t = benchmark.best_threshold([0.3, 0.4], [0.05, 0.1])
    assert 0.1 < t <= 0.3
    # overlapping: one noisy negative above a positive; precision-weighted F keeps it out
    t2 = benchmark.best_threshold([0.2, 0.3, 0.35], [0.25, 0.05])
    assert 0.25 < t2 <= 0.3
    assert benchmark.best_threshold([0.1], [0.2, 0.3]) is not None
    assert benchmark.best_threshold([], [0.2]) is None


def _tag_fixture(conn):
    """dog has a weakly dog-like negative, cat a different score range: one global threshold cannot fit both."""
    add(conn, "p1", _vec("dog"))
    add(conn, "p2", mix("dog", "z1", 0.5))
    add(conn, "n1", mix("dog", "z2", 3.0))
    add(conn, "n2", _vec("z3"))
    add(conn, "c1", mix("cat", "z4", 0.8))
    add(conn, "c2", mix("cat", "z5", 0.9))
    add(conn, "c3", _vec("z6"))
    return benchmark.validate_labels({"tags": [
        {"photo": "p1", "present": ["dog"], "absent": ["cat"]},
        {"photo": "p2", "present": ["dog"], "absent": ["cat"]},
        {"photo": "n1", "absent": ["dog", "cat"]},
        {"photo": "n2", "absent": ["dog", "cat"]},
        {"photo": "c1", "present": ["cat"], "absent": ["dog"]},
        {"photo": "c2", "present": ["cat"], "absent": ["dog"]},
        {"photo": "c3", "absent": ["cat", "dog"]},
        {"photo": "p1", "present": ["not a vocab label"]},
    ]})


def test_calibration_improves_tag_precision_and_is_stored(conn):
    labels = _tag_fixture(conn)
    # a threshold that is far too loose produces false positives
    before = benchmark.run_tags(conn, labels, fake_text, thresholds={"dog": -1.0, "cat": -1.0})
    assert before["fp"] > 0 and before["unknown_labels"] == ["not a vocab label"]
    res = benchmark.calibrate_and_save(conn, labels, fake_text)
    assert set(res["thresholds"]) == {"dog", "cat"} and res["saved"] == 2
    assert autotag.calibrated_thresholds(conn) == res["thresholds"]
    after = benchmark.run_tags(conn, labels, fake_text)
    assert after["fp"] == 0 and after["fn"] == 0
    assert after["precision"] == 1.0 and after["recall"] == 1.0
    assert after["per_label"]["dog"]["threshold"] == pytest.approx(res["thresholds"]["dog"], abs=1e-4)
    assert "Calibrated 2 label" in benchmark.format_calibration(res)


def test_labels_with_too_little_data_are_skipped(conn):
    add(conn, "p1", _vec("dog"))
    add(conn, "n1", _vec("z"))
    labels = benchmark.validate_labels(
        {"tags": [{"photo": "p1", "present": ["dog"]}, {"photo": "n1", "absent": ["dog"]}]})
    res = benchmark.calibrate(conn, labels, fake_text)
    assert res["thresholds"] == {} and res["skipped"]["dog"] == {"positives": 1, "negatives": 1}
    assert benchmark.calibrate(conn, labels, fake_text, min_pos=1, min_neg=1)["thresholds"].keys() == {"dog"}


def test_calibrated_threshold_drives_tagging_and_reset(conn):
    e = mix("dog", "z1", 1.5)
    labels, thr, m = autotag.label_vectors(conn, fake_text)
    i = labels.index("dog")
    score = float(m[i] @ e)
    assert thr[i] == pytest.approx(vocab.DEFAULT_THRESHOLD)
    autotag.save_calibrated_thresholds(conn, {"dog": score + 0.2, "not-a-label": 0.9})
    _l, thr2, _m = autotag.label_vectors(conn)
    assert thr2[i] == pytest.approx(score + 0.2, abs=1e-4)
    assert i not in [j for j, _s in autotag.pick(e, m, thr2)]
    autotag.save_calibrated_thresholds(conn, {})
    assert autotag.label_vectors(conn)[1][i] == pytest.approx(vocab.DEFAULT_THRESHOLD)


def test_calibrated_thresholds_survive_garbage(conn):
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (autotag.THRESHOLDS_KEY, "{not json"))
    conn.commit()
    assert autotag.calibrated_thresholds(conn) == {}


def test_face_pair_accuracy(conn):
    for h in ("a", "b", "c"):
        add(conn, h, _vec(h))
    for h, cl in (("a", 1), ("b", 1), ("c", 2)):
        conn.execute("INSERT INTO face(hash,emb,cluster) VALUES (?,?,?)",
                     (h, store.vec_to_blob(_vec(h), np.float32), cl))
    conn.commit()
    labels = benchmark.validate_labels({"faces": [
        {"photos": ["a", "b"], "same": True},
        {"photos": ["a", "c"], "same": False},
        {"photos": ["b", "c"], "same": True},   # wrong on purpose
    ]})
    r = benchmark.run_faces(conn, labels)
    assert r == {"pairs": 3, "correct": 2, "accuracy": pytest.approx(2 / 3)}


def test_worker_cli_benchmark_and_calibrate(conn, tmp_path, monkeypatch, capsys):
    from photovault.ai import worker
    labels = _tag_fixture(conn)
    lf = tmp_path / "labels.json"
    lf.write_text(json.dumps({"tags": labels["tags"], "search": [{"query": "dog", "expected": ["p1"]}]}),
                  encoding="utf-8")

    class FakeSig:
        def __init__(self, *_a):
            pass

        def encode_text(self, texts):
            return fake_text(texts)

    fake_models = types.ModuleType("photovault.ai.models")  # the real one needs onnxruntime + model files
    fake_models.SigLIP = FakeSig
    monkeypatch.setitem(sys.modules, "photovault.ai.models", fake_models)
    monkeypatch.setattr(store, "connect", lambda *a, **k: conn)
    assert worker.main(["calibrate", str(lf)]) is None
    assert "Calibrated" in capsys.readouterr().out
    worker.main(["benchmark", str(lf)])
    out = capsys.readouterr().out
    assert "precision: 100.0%" in out and "recall@1" in out
    worker.main(["calibrate", "--reset"])
    assert autotag.calibrated_thresholds(conn) == {}
