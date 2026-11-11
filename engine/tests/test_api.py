import json
import re
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from clipapp_engine.api import service
from clipapp_engine.api.app import create_app
from clipapp_engine.api.docs import api_markdown, openapi_schema
from clipapp_engine.transcribe.device import DeviceChoice
from clipapp_engine.transcribe.transcript import Transcript, Word

TOKEN = "secreto-de-prueba"
FINAL = ("done", "failed", "cancelled")
DOCS = Path(__file__).parents[2] / "docs" / "api.md"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(TOKEN, tmp_path / "api.db")
    with TestClient(app, headers={"X-ClipApp-Token": TOKEN}) as c:
        c.put("/settings", json={"output_dir": str(tmp_path / "salida ñ")})
        yield c


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    calls: list[tuple[float, float]] = []

    def transcribe(_m: Path, _t: int, start: float, end: float, *_a: Any) -> Transcript:
        calls.append((start, end))
        words = [Word(text="hola", start_s=start + 1, end_s=start + 2, probability=0.9)]
        return Transcript(model="fake", language="es", words=words)

    monkeypatch.setattr(service, "transcribe_range", transcribe)
    monkeypatch.setattr(service, "pick_device", lambda *_a: DeviceChoice("fake", "cpu", "int8"))
    return calls


def wait_job(c: TestClient, job_id: int, timeout: float = 180) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job: dict[str, Any] = next(j for j in c.get("/exports").json() if j["id"] == job_id)
        if job["status"] in FINAL:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def last_job(c: TestClient, kind: str, target: int) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = c.get("/exports").json()
    return next(j for j in jobs if j["kind"] == kind and j["target_id"] == target)


def import_video(c: TestClient, video: Path) -> dict[str, Any]:
    r = c.post("/projects", json={"path": str(video)})
    assert r.status_code == 201, r.text
    project: dict[str, Any] = r.json()
    assert wait_job(c, last_job(c, "prepare", project["id"])["id"])["status"] == "done"
    return project


@pytest.fixture
def project(client: TestClient, av_sample: Path) -> dict[str, Any]:
    return import_video(client, av_sample)


def clip_body(**extra: Any) -> dict[str, Any]:
    return {"name": "Jugada épica", "start_s": 2.0, "end_s": 18.0, **extra}


def test_every_route_needs_token(tmp_path: Path) -> None:
    app = create_app(TOKEN, tmp_path / "auth.db", start_worker=False)
    with TestClient(app) as c:
        routes = [(m, p) for p, item in app.openapi()["paths"].items() for m in item]
        assert len(routes) > 30
        for method, route in routes:
            path = re.sub(r"{[^}]+}", "1", route)
            for headers in ({}, {"X-ClipApp-Token": "wrong"}):
                r = c.request(method, path, headers=headers)
                assert r.status_code == 401, (method, route)
        assert c.get("/projects/1/media/proxy?token=wrong").status_code == 401
        with pytest.raises(WebSocketDenialResponse) as denied, c.websocket_connect("/events"):
            pass
        assert denied.value.status_code == 401


def test_health_and_cors(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    pre = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:1420",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-clipapp-token",
        },
    )
    assert pre.headers["access-control-allow-origin"] == "http://localhost:1420"
    evil = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


def test_settings(client: TestClient, tmp_path: Path) -> None:
    body = client.get("/settings").json()
    assert body["output_dir"] == str(tmp_path / "salida ñ")
    body |= {"device": "cpu", "glossary": {"terms": ["Valorant"], "replacements": {"a": "b"}}}
    assert client.put("/settings", json=body).json() == body
    assert client.get("/settings").json() == body
    assert client.put("/settings", json={**body, "device": "tpu"}).status_code == 422


def test_styles(client: TestClient) -> None:
    styles = client.get("/styles").json()
    assert [s["name"] for s in styles] == ["Shorts", "Limpio", "Gamer"]
    assert all(s["builtin"] for s in styles)
    new = {k: v for k, v in styles[0].items() if k not in ("id", "builtin")} | {"name": "Mío"}
    r = client.post("/styles", json=new)
    assert r.status_code == 201
    mine = r.json()
    assert not mine["builtin"]
    assert client.patch(f"/styles/{mine['id']}", json={"size": 90}).json()["size"] == 90
    assert client.patch(f"/styles/{mine['id']}", json={"primary_color": "red"}).status_code == 422
    assert client.post("/styles", json={**new, "mode": "karaoke"}).status_code == 422
    assert client.delete(f"/styles/{styles[0]['id']}").status_code == 409
    assert client.delete(f"/styles/{mine['id']}").status_code == 204
    assert client.delete(f"/styles/{mine['id']}").status_code == 404
    assert client.patch("/styles/999", json={}).status_code == 404


def test_project_errors(client: TestClient, tmp_path: Path, video_no_audio: Path) -> None:
    assert client.post("/projects", json={"path": str(tmp_path / "no.mkv")}).status_code == 404
    assert client.post("/projects", json={"path": str(video_no_audio)}).status_code == 422
    assert client.post("/projects", json={}).status_code == 422
    for path in ("/projects/9", "/projects/9/silence", "/projects/9/clips"):
        assert client.get(path).status_code == 404
    assert client.patch("/projects/9", json={"name": "x"}).status_code == 404
    assert client.delete("/projects/9").status_code == 404
    assert client.get("/projects/9/media/proxy").status_code == 404


def test_project_lifecycle(client: TestClient, project: dict[str, Any], av_sample: Path) -> None:
    pid = project["id"]
    got = client.get(f"/projects/{pid}").json()
    assert got["name"] == "sample"
    assert got["media"]["prepare_status"] == "done"
    assert (got["media"]["mic_track"], got["media"]["game_track"]) == (1, 2)
    assert len(got["media"]["audio_tracks"]) == 3
    assert [p["id"] for p in client.get("/projects").json()] == [pid]

    r = client.patch(f"/projects/{pid}", json={"name": "Partida", "game_track": 0})
    assert (r.json()["name"], r.json()["media"]["game_track"]) == ("Partida", 0)
    assert client.patch(f"/projects/{pid}", json={"mic_track": 5}).status_code == 422
    assert client.patch(f"/projects/{pid}", json={"name": ""}).status_code == 422


def test_media_range(client: TestClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    proxy = Path(project["media"]["cache_dir"]) / "proxy.mp4"
    full = client.get(f"/projects/{pid}/media/proxy")
    assert full.status_code == 200
    assert full.content == proxy.read_bytes()
    part = client.get(f"/projects/{pid}/media/proxy", headers={"Range": "bytes=100-299"})
    assert part.status_code == 206
    assert part.content == proxy.read_bytes()[100:300]
    assert part.headers["content-range"].startswith("bytes 100-299/")
    no_header = TestClient(client.app)
    assert no_header.get(f"/projects/{pid}/media/proxy?token={TOKEN}").status_code == 200
    waveform = client.get(f"/projects/{pid}/media/waveform").json()
    assert waveform["peaks_per_second"] == 50
    assert client.get(f"/projects/{pid}/media/thumbnails").content[:2] == b"\xff\xd8"
    assert client.get(f"/projects/{pid}/media/thumbnails/index").json()["count"] == 2


def test_track_preview(client: TestClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    r = client.get(f"/projects/{pid}/media/tracks/1/preview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mp4"
    assert b"ftyp" in r.content[:16]
    assert (Path(project["media"]["cache_dir"]) / "preview_track1.m4a").is_file()
    assert client.get(f"/projects/{pid}/media/tracks/9/preview").status_code == 404


def test_mic_change_cancels_pending_prepare(tmp_path: Path, av_sample: Path) -> None:
    app = create_app(TOKEN, tmp_path / "idle.db", start_worker=False)
    with TestClient(app, headers={"X-ClipApp-Token": TOKEN}) as c:
        pid = c.post("/projects", json={"path": str(av_sample)}).json()["id"]
        first = last_job(c, "prepare", pid)
        c.patch(f"/projects/{pid}", json={"mic_track": 0})
        jobs = [j for j in c.get("/exports").json() if j["kind"] == "prepare"]
        assert [j["status"] for j in jobs] == ["queued", "cancelled"]
        assert jobs[1]["id"] == first["id"]


def test_silence_keeps_unchanged_toggles(client: TestClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    state = client.get(f"/projects/{pid}/silence").json()
    ranges = state["ranges"]
    bounds = [t for r in ranges for t in (r["start_s"], r["end_s"])]
    assert bounds == pytest.approx([3.15, 4.85, 12.15, 14.85], abs=0.05)
    off = client.patch(f"/silence-ranges/{ranges[0]['id']}", json={"enabled": False}).json()
    assert off["enabled"] is False
    assert client.patch("/silence-ranges/999", json={"enabled": False}).status_code == 404

    same = client.put(f"/projects/{pid}/silence", json=state["settings"]).json()
    assert [r["enabled"] for r in same["ranges"]] == [False, True]
    assert same["stats"]["cuts"] == 1  # the disabled silence is kept in the export

    # min_keep_s does not move these bounds; padding moves every bound.
    kept = client.put(f"/projects/{pid}/silence", json={**state["settings"], "min_keep_s": 0.1})
    assert [r["enabled"] for r in kept.json()["ranges"]] == [False, True]
    moved = client.put(f"/projects/{pid}/silence", json={**state["settings"], "padding_s": 0.3})
    assert [r["enabled"] for r in moved.json()["ranges"]] == [True, True]
    assert client.get(f"/projects/{pid}/silence").json()["settings"]["padding_s"] == 0.3
    bad = client.put(f"/projects/{pid}/silence", json={**state["settings"], "threshold_db": 5})
    assert bad.status_code == 422

    suggestion = client.get(f"/projects/{pid}/silence/suggest").json()
    assert suggestion["threshold_db"] < 0


def test_clips_and_transcripts(
    client: TestClient, project: dict[str, Any], fake_whisper: list[tuple[float, float]]
) -> None:
    pid = project["id"]
    shorts = client.get("/styles").json()[0]["id"]
    r = client.post(f"/projects/{pid}/clips", json=clip_body(style_id=shorts))
    assert r.status_code == 201, r.text
    clip = r.json()
    assert (clip["aspect"], clip["crop_mode"], clip["normalize"]) == ("9:16", "crop", True)
    assert client.post(f"/projects/{pid}/clips", json=clip_body(end_s=10)).status_code == 422
    assert client.post(f"/projects/{pid}/clips", json=clip_body(end_s=25)).status_code == 422
    assert client.post(f"/projects/{pid}/clips", json=clip_body(style_id=999)).status_code == 422
    assert client.post(f"/projects/{pid}/clips", json=clip_body(aspect="4:3")).status_code == 422
    assert [c["id"] for c in client.get(f"/projects/{pid}/clips").json()] == [clip["id"]]
    assert client.get(f"/clips/{clip['id']}").json() == clip

    cid = clip["id"]
    assert client.get(f"/clips/{cid}/transcript").status_code == 404
    assert client.put(f"/clips/{cid}/transcript", json={"words": []}).status_code == 404
    job = client.post(f"/clips/{cid}/transcript").json()
    assert wait_job(client, job["id"])["status"] == "done"
    transcript = client.get(f"/clips/{cid}/transcript").json()
    assert (transcript["model"], transcript["edited"], transcript["stale"]) == (
        "fake",
        False,
        False,
    )
    assert fake_whisper == [(2.0, 18.0)]

    words = [{"text": "Hola", "start_s": 3.0, "end_s": 4.0, "probability": 1.0}]
    edited = client.put(f"/clips/{cid}/transcript", json={"words": words}).json()
    assert edited["edited"] is True
    assert client.get(f"/clips/{cid}/transcript").json()["words"] == words

    assert client.patch(f"/clips/{cid}", json={"name": "Otro"}).json()["name"] == "Otro"
    assert client.get(f"/clips/{cid}/transcript").json()["stale"] is False
    assert client.patch(f"/clips/{cid}", json={"start_s": 1.0}).json()["start_s"] == 1.0
    assert client.get(f"/clips/{cid}/transcript").json()["stale"] is True
    assert client.patch(f"/clips/{cid}", json={"end_s": 5.0}).status_code == 422
    assert client.patch(f"/clips/{cid}", json={"start_s": None}).status_code == 422
    assert client.patch(f"/clips/{cid}", json={"style_id": None}).json()["style_id"] is None

    assert client.delete(f"/clips/{cid}").status_code == 204
    for method in ("GET", "DELETE"):
        assert client.request(method, f"/clips/{cid}").status_code == 404
    assert client.post(f"/clips/{cid}/transcript").status_code == 404


def test_export_clip_with_edited_subtitles(
    client: TestClient, project: dict[str, Any], fake_whisper: list[tuple[float, float]]
) -> None:
    pid = project["id"]
    style = client.get("/styles").json()[0]["id"]
    clip = client.post(f"/projects/{pid}/clips", json=clip_body(style_id=style)).json()
    words = [{"text": "editado", "start_s": 3.0, "end_s": 4.0, "probability": 1.0}]
    client.post(f"/clips/{clip['id']}/transcript")
    wait_job(client, last_job(client, "transcribe", clip["id"])["id"])
    client.put(f"/clips/{clip['id']}/transcript", json={"words": words})

    jobs = client.post("/exports", json={"kind": "clip", "target_id": clip["id"]}).json()
    job = wait_job(client, jobs[0]["id"])
    assert job["status"] == "done", job["error"]
    out = Path(job["output_path"])
    assert out.is_file()
    assert out.name == "sample_Jugada épica_9x16.mp4"
    assert fake_whisper == [(2.0, 18.0)]  # the edited transcript was used, not a new one


def test_export_batch_full_cancel_retry(client: TestClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    clip = client.post(f"/projects/{pid}/clips", json=clip_body()).json()
    assert client.post("/exports", json=[{"kind": "clip", "target_id": 999}]).status_code == 404
    batch = [{"kind": "full", "target_id": pid}, {"kind": "clip", "target_id": clip["id"]}]
    jobs = client.post("/exports", json=batch).json()
    assert [j["kind"] for j in jobs] == ["full", "clip"]
    cancelled = client.post(f"/exports/{jobs[1]['id']}/cancel").json()
    assert cancelled["status"] in ("cancelled", "running")
    full = wait_job(client, jobs[0]["id"])
    assert full["status"] == "done", full["error"]
    assert Path(full["output_path"]).name == "sample_sin silencios_16x9.mp4"
    assert wait_job(client, jobs[1]["id"])["status"] == "cancelled"

    assert client.post(f"/exports/{jobs[0]['id']}/cancel").status_code == 409
    assert client.post(f"/exports/{jobs[0]['id']}/retry").status_code == 409
    assert client.post(f"/exports/{jobs[1]['id']}/retry").json()["status"] in ("queued", "running")
    assert wait_job(client, jobs[1]["id"])["status"] == "done"
    assert client.post("/exports/999/cancel").status_code == 404
    assert client.post("/exports/999/retry").status_code == 404
    assert client.post("/exports", json={"kind": "render", "target_id": 1}).status_code == 422


def test_failed_job_reports_error(client: TestClient, project: dict[str, Any]) -> None:
    clip = client.post(f"/projects/{project['id']}/clips", json=clip_body()).json()
    client.put("/settings", json={**client.get("/settings").json(), "encoder": "no_such_enc"})
    job = client.post("/exports", json={"kind": "clip", "target_id": clip["id"]}).json()[0]
    failed = wait_job(client, job["id"])
    assert failed["status"] == "failed"
    assert "no_such_enc" in failed["error"]


def test_mic_change_requeues_prepare(
    client: TestClient, project: dict[str, Any], fake_whisper: list[tuple[float, float]]
) -> None:
    pid = project["id"]
    clip = client.post(f"/projects/{pid}/clips", json=clip_body()).json()
    client.post(f"/clips/{clip['id']}/transcript")
    wait_job(client, last_job(client, "transcribe", clip["id"])["id"])
    before = last_job(client, "prepare", pid)["id"]
    client.patch(f"/projects/{pid}", json={"mic_track": 1})  # unchanged: nothing queued
    assert last_job(client, "prepare", pid)["id"] == before
    assert client.patch(f"/projects/{pid}", json={"mic_track": 0}).json()["media"]["mic_track"] == 0
    job = last_job(client, "prepare", pid)
    assert job["id"] != before
    assert client.get(f"/clips/{clip['id']}/transcript").json()["stale"] is True
    assert wait_job(client, job["id"])["status"] == "done"


def test_relink(
    client: TestClient, project: dict[str, Any], tmp_path: Path, video_no_audio: Path
) -> None:
    pid = project["id"]
    moved = Path(shutil.copy2(project["media"]["path"], tmp_path / "movido ó.mkv"))
    r = client.post(f"/projects/{pid}/relink", json={"path": str(moved)})
    assert r.json()["media"]["path"] == str(moved.resolve())
    assert (
        client.post(f"/projects/{pid}/relink", json={"path": str(video_no_audio)}).status_code
        == 409
    )
    assert client.post(f"/projects/{pid}/relink", json={"path": "nope.mkv"}).status_code == 422


def test_delete_keeps_original(client: TestClient, av_sample: Path, tmp_path: Path) -> None:
    video = Path(shutil.copy(av_sample, tmp_path / "copia.mkv"))  # new mtime: its own cache
    project = import_video(client, video)
    cache = Path(project["media"]["cache_dir"])
    assert cache.is_dir()
    assert client.delete(f"/projects/{project['id']}").status_code == 204
    assert client.get(f"/projects/{project['id']}").status_code == 404
    assert not cache.exists()
    assert video.is_file()


def test_events_websocket(client: TestClient, project: dict[str, Any]) -> None:
    with client.websocket_connect(f"/events?token={TOKEN}") as ws:
        client.patch(f"/projects/{project['id']}", json={"name": "nuevo"})
        assert ws.receive_json() == {"type": "project_updated", "id": project["id"]}
        clip = client.post(f"/projects/{project['id']}/clips", json=clip_body()).json()
        ws.receive_json()
        client.post("/exports", json={"kind": "clip", "target_id": clip["id"]})
        seen: list[dict[str, Any]] = []
        while not (seen and seen[-1].get("status") in FINAL):
            seen.append(ws.receive_json())
        kinds = {e["type"] for e in seen}
        assert {"job_status", "job_progress"} <= kinds
        assert seen[-1]["status"] == "done"
        assert seen[-1]["output_path"]


def test_restart_interrupts_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.db"
    app = create_app(TOKEN, db_path, start_worker=False)
    with TestClient(app, headers={"X-ClipApp-Token": TOKEN}) as c:
        app.state.jobs.submit("full", 1)
    with TestClient(create_app(TOKEN, db_path), headers={"X-ClipApp-Token": TOKEN}) as c:
        job = c.get("/exports").json()[0]
    assert (job["status"], job["error"]) == ("failed", "interrupted")


def test_docs_are_current() -> None:
    assert DOCS.read_text(encoding="utf-8") == api_markdown(openapi_schema()), (
        "docs/api.md is stale: run `uv run clipapp-engine openapi --markdown -o ../docs/api.md`"
    )
    assert json.dumps(openapi_schema())  # serializable for openapi-typescript
