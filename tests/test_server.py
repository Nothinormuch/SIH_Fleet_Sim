"""End-to-end tests for the dashboard's BIOS_4 endpoints, over real HTTP.

These drive an actual `ThreadingHTTPServer` on a loopback port rather than calling the
handler methods directly, because most of what can go wrong here is not in the handler
bodies: it is Content-Length handling, a training thread that blocks the request loop, a
job registry mutated from two threads, and a download that forgets its headers. None of
those are visible if you unit-test the functions.

Kept fast on purpose - the training jobs here are 4 genomes over one 30 s episode, which
is enough to prove the machinery turns. Whether evolution actually *learns* anything is a
question for the benchmark, not for a test suite.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from backend.server import Handler
from http.server import ThreadingHTTPServer
from src.bios4 import FEATURES, random_model


@pytest.fixture(scope="module")
def base_url():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.status, json.loads(r.read()), dict(r.headers)


def _post(url: str, payload, raw: bytes | None = None):
    data = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, json.loads(r.read())


def _run(base_url: str, **kw):
    """`/api/run` takes a validated JSON body, not a query string."""
    return _post(f"{base_url}/api/run", kw)


def _expect_error(fn, code: int):
    """urllib raises on 4xx/5xx, and the BODY is the interesting part - these endpoints
    are supposed to explain themselves, so the tests assert on what they say."""
    with pytest.raises(urllib.error.HTTPError) as exc:
        fn()
    assert exc.value.code == code
    return json.loads(exc.value.read())


# ------------------------------------------------------------------ basics


def test_scenarios_lists_bios4(base_url):
    _, body, _ = _get(f"{base_url}/api/scenarios")
    assert "BIOS_4" in body["policies"]


def test_bios4_without_a_model_is_refused_with_a_useful_message(base_url):
    """BIOS_4 with no model is *legal* - it degrades to always-hold - which is exactly
    why the endpoint refuses it. An untrained control rendered as a trained policy is
    worse than an error, because nothing on screen says which one you are looking at."""
    body = _expect_error(
        lambda: _run(base_url, policy="BIOS_4", scenario="crossing_chokepoint"), 400)
    assert "train one or upload one" in body["error"]


def test_unknown_model_id_says_models_are_in_memory(base_url):
    body = _expect_error(
        lambda: _run(base_url, policy="BIOS_4", model="deadbeef1234"), 404)
    assert "restarts" in body["hint"]


# ------------------------------------------------------------------ upload


def test_upload_then_run(base_url):
    model = random_model(seed=42)
    status, body = _post(f"{base_url}/api/model", None,
                         raw=model.to_json({"note": "test"}).encode())
    assert status == 200
    assert body["params"] == len(model.w)
    mid = body["model"]

    status, run = _run(base_url, policy="BIOS_4", robots=2, duration=20, model=mid)
    assert status == 200
    assert run["meta"]["policy"] == "BIOS_4"
    assert run["meta"]["model"]["note"] == "test", "provenance did not survive the upload"
    assert run["meta"]["has_manager"] is False, "BIOS_4 is peer-to-peer by design"
    assert run["frames"], "a run with no frames is not a run"


def test_uploading_junk_is_a_400_that_explains_itself(base_url):
    body = _expect_error(
        lambda: _post(f"{base_url}/api/model", None, raw=b"<html>nope</html>"), 400)
    assert "JSON" in body["error"]


def test_uploading_a_model_from_a_different_feature_set_is_refused(base_url):
    """The one that matters. A renumbered input does not crash - it quietly computes a
    different function, and the run looks like a badly trained policy rather than a
    loading bug."""
    d = random_model(seed=1).to_dict()
    d["features"] = list(FEATURES)[:-1] + ["a_feature_that_no_longer_exists"]
    body = _expect_error(
        lambda: _post(f"{base_url}/api/model", None, raw=json.dumps(d).encode()), 400)
    assert "different observation layout" in body["error"]
    assert "Retrain" in body["error"]


def test_an_empty_upload_is_rejected_before_it_is_read(base_url):
    _expect_error(lambda: _post(f"{base_url}/api/model", None, raw=b""), 413)


# ------------------------------------------------------------------ training


def _train(base_url, **over):
    payload = {"population": 4, "generations": 1, "robots": 2, "hidden": 4,
               "workers": 1, "episodes": [[0, 30.0]]}
    payload.update(over)
    return _post(f"{base_url}/api/train", payload)


def _wait_for(base_url, job, want=("done", "cancelled", "failed"), timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, st, _ = _get(f"{base_url}/api/train/status?job={job}")
        if st["state"] in want:
            return st
        time.sleep(0.25)
    raise AssertionError(f"job {job} did not finish within {timeout}s")


def test_training_runs_and_produces_a_downloadable_model(base_url):
    status, started = _train(base_url)
    assert status == 202
    job = started["job"]

    st = _wait_for(base_url, job)
    assert st["state"] == "done", st.get("error")
    assert st["history"], "no per-generation history was recorded"
    assert st["history"][0]["episodes"] > 0

    # The download must arrive as a file, not as a page. This has a habit of regressing
    # into a JSON body that the browser renders instead of saving.
    with urllib.request.urlopen(f"{base_url}/api/train/model?job={job}", timeout=60) as r:
        assert r.status == 200
        assert "attachment" in r.headers.get("Content-Disposition", "")
        model = json.loads(r.read())
    assert model["format"] == "bios4-mlp"
    assert model["features"] == list(FEATURES)
    assert model["meta"]["algorithm"].startswith("mirrored-sampling")
    assert model["meta"]["eval_seeds_withheld"] == [8, 9, 10, 11], \
        "the model must record which seeds it was NOT trained on"

    # And it must be loadable straight back, which is the whole point of the format.
    status, up = _post(f"{base_url}/api/model", None, raw=json.dumps(model).encode())
    assert status == 200 and up["params"] == len(model["weights"])


def test_the_server_still_answers_while_training(base_url):
    """Training holds the machine for minutes. If it also held _SIM_LOCK or blocked the
    request loop, the dashboard would look hung for the entire run - which is
    indistinguishable, from a browser, from a crash."""
    _, started = _train(base_url, generations=3, episodes=[[0, 60.0]])
    job = started["job"]
    try:
        t0 = time.perf_counter()
        status, body, _ = _get(f"{base_url}/api/scenarios")
        assert status == 200 and body["policies"]
        assert time.perf_counter() - t0 < 10.0, "the server stalled while training"
    finally:
        _post(f"{base_url}/api/train/cancel?job={job}", {})
        _wait_for(base_url, job)


def test_only_one_training_run_at_a_time(base_url):
    _, started = _train(base_url, generations=3, episodes=[[0, 60.0]])
    job = started["job"]
    try:
        body = _expect_error(lambda: _train(base_url), 409)
        assert body["job"] == job
    finally:
        _post(f"{base_url}/api/train/cancel?job={job}", {})
        _wait_for(base_url, job)


def test_cancelling_still_returns_the_best_genome_found(base_url):
    """Cancelling must not discard the work. People stop a run precisely when it has
    already learned something and they do not want to wait for the rest."""
    _, started = _train(base_url, generations=8, episodes=[[0, 30.0]])
    job = started["job"]
    _wait_for(base_url, job, want=("done", "cancelled", "failed"), timeout=30) \
        if False else None
    # Let one generation land so there is something to keep, then stop it.
    deadline = time.time() + 120
    while time.time() < deadline:
        _, st, _ = _get(f"{base_url}/api/train/status?job={job}")
        if st["history"] or st["state"] != "running":
            break
        time.sleep(0.25)
    status, body = _post(f"{base_url}/api/train/cancel?job={job}", {})
    assert status in (200, 202)
    st = _wait_for(base_url, job)
    assert st["state"] in ("cancelled", "done")
    _, model, _ = _get(f"{base_url}/api/train/model?job={job}")
    assert len(model["weights"]) > 0


def test_training_rejects_held_out_seeds(base_url):
    """The guard that keeps the headline number honest. Training on an evaluation seed
    turns 'BIOS_4 beats BIOS_1.0.0' into a memorisation score, and it is the first thing
    anyone evaluating this will check."""
    body = _expect_error(lambda: _train(base_url, episodes=[[9, 30.0]]), 400)
    assert "memorisation" in body["error"]


def test_unknown_job_is_a_404(base_url):
    _expect_error(lambda: _get(f"{base_url}/api/train/status?job=nope"), 404)
    _expect_error(lambda: _get(f"{base_url}/api/train/model?job=nope"), 404)
