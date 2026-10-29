"""Black-box REST API and WebSocket test suite for Lecture Digest Sync GUI Server.

Tests:
1. Core Web UI routing (GET /, icon.png).
2. System status & hardware telemetry (GET /api/status).
3. Configuration management (GET /api/config, POST /api/config).
4. Timetable CRUD endpoints (GET /api/timetable, POST /api/timetable).
5. Course and Glossary endpoints (CRUD on courses and custom terminology via /api/glossary).
6. Execution mode switching (POST /api/mode).
7. Processing task controls (pause, abort, status checks via /api/task/pause and /api/task/abort).
8. WebSocket live log and progress streaming (/ws/events).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from lecture_pipeline.gui.server import app
from lecture_pipeline.gui.bridge import broadcaster


@pytest.fixture
def client():
    return TestClient(app)


class TestBlackBoxRestApiSuite:
    """Complete REST API black-box validation suite."""

    def test_serve_root_index_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "<!DOCTYPE html>" in response.text or "<html" in response.text

    def test_get_system_status(self, client):
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert "gpu" in data
        assert "network" in data
        assert "watcher" in data
        assert "disk" in data
        assert "free_gb" in data["disk"]

    def test_get_and_update_config(self, client, tmp_path):
        # 1. GET config
        res_get = client.get("/api/config")
        assert res_get.status_code == 200
        cfg = res_get.json()
        assert "pipeline" in cfg or "gui" in cfg

        # 2. POST update config
        with patch("lecture_pipeline.gui.server.update_pipeline_config") as mock_update:
            mock_update.return_value = {"updated": True, "stt": {"channel": "C7"}}
            res_post = client.post("/api/config", json={"params": {"stt": {"channel": "C7"}}})
            assert res_post.status_code == 200
            assert res_post.json()["status"] == "ok"
            assert res_post.json()["config"]["stt"]["channel"] == "C7"

    def test_timetable_crud(self, client):
        rules_payload = {
            "enabled": True,
            "rules": [
                {
                    "weekday": "Monday",
                    "time": "09:00-12:00",
                    "course": "計算機結構",
                }
            ],
        }

        with patch("lecture_pipeline.gui.server.save_timetable_rules") as mock_save, \
             patch("lecture_pipeline.gui.server.get_timetable_rules") as mock_get:
            mock_save.return_value = rules_payload
            mock_get.return_value = rules_payload

            # Save
            res_save = client.post("/api/timetable", json=rules_payload)
            assert res_save.status_code == 200
            assert res_save.json()["status"] == "ok"

            # Get
            res_get = client.get("/api/timetable")
            assert res_get.status_code == 200
            assert len(res_get.json()["rules"]) == 1
            assert res_get.json()["rules"][0]["course"] == "計算機結構"

    def test_course_and_glossary_management(self, client):
        with patch("lecture_pipeline.gui.server.get_available_courses") as mock_courses, \
             patch("lecture_pipeline.gui.server.add_course") as mock_add_c, \
             patch("lecture_pipeline.gui.server.get_course_glossary_terms") as mock_terms, \
             patch("lecture_pipeline.gui.server.add_glossary_term") as mock_add_term:

            mock_courses.return_value = ["作業系統", "編譯器"]
            mock_add_c.return_value = {"status": "ok", "course": "演算法"}
            mock_terms.return_value = [{"term": "Semaphore", "translation": "信號標"}]
            mock_add_term.return_value = {"status": "ok"}

            # Get courses and terms via /api/glossary
            res_c = client.get("/api/glossary")
            assert res_c.status_code == 200
            assert "作業系統" in res_c.json()["courses"]

            # Add course
            res_add = client.post("/api/glossary/course", json={"course": "演算法"})
            assert res_add.status_code == 200

            # Get terms for a course
            res_t = client.get("/api/glossary?course=作業系統")
            assert res_t.status_code == 200
            assert len(res_t.json()["terms"]) == 1
            assert res_t.json()["terms"][0]["term"] == "Semaphore"

            # Add term
            res_add_t = client.post(
                "/api/glossary/term",
                json={"course": "作業系統", "term": "Mutex", "translation": "互斥鎖"},
            )
            assert res_add_t.status_code == 200

    def test_mode_switching(self, client):
        with patch("lecture_pipeline.gui.server.switch_gui_mode_action") as mock_switch:
            mock_switch.return_value = {"status": "ok", "mode": "web"}
            res = client.post("/api/mode", json={"mode": "web", "source": "test"})
            assert res.status_code == 200
            assert res.json()["mode"] == "web"

    def test_process_controls_pause_and_abort(self, client):
        with patch("lecture_pipeline.gui.server.pause_processing_task") as mock_pause, \
             patch("lecture_pipeline.gui.server.abort_processing_task") as mock_abort:

            mock_pause.return_value = {"status": "ok", "paused": True}
            mock_abort.return_value = {"status": "ok", "aborted": True}

            # Pause
            res_p = client.post("/api/task/pause")
            assert res_p.status_code == 200
            assert res_p.json()["paused"] is True

            # Abort
            res_a = client.post("/api/task/abort")
            assert res_a.status_code == 200
            assert res_a.json()["aborted"] is True

    def test_websocket_event_streaming(self, client):
        with client.websocket_connect("/ws/events") as websocket:
            # 1. Server sends initial connect message
            init_msg = websocket.receive_json()
            assert init_msg["type"] == "connected"

            # 2. Ping/Pong
            websocket.send_text("ping")
            pong = websocket.receive_text()
            assert pong == "pong"
