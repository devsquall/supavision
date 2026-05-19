"""Tests for the Incident lightweight-state feature (P2 #12)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Incident, IncidentState, Resource, User
from supavision.web.auth import generate_api_key, hash_password
from supavision.web.routes import health_router
from supavision.web.routes import router as api_router


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test_incidents.db")
    yield s
    s.close()


@pytest.fixture
def app(store):
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(api_router)
    app.state.store = store
    app.state.engine = MagicMock()
    app.state.scheduler = MagicMock()
    return app


@pytest.fixture
def client(app, store):
    key_id, raw, key_hash = generate_api_key()
    store.save_api_key(key_id, key_hash, label="t", role="admin")
    return TestClient(app, headers={"x-api-key": raw})


@pytest.fixture
def resource(store):
    r = Resource(name="srv", resource_type="server")
    store.save_resource(r)
    return r


@pytest.fixture
def real_user(store) -> User:
    u = User(
        email="real@test.com",
        password_hash=hash_password("RealUser123!"),
        name="Real",
        role="admin",
    )
    store.create_user(u)
    return u


class TestIncidentStore:
    def test_save_get_round_trip(self, store, resource):
        inc = Incident(resource_id=resource.id, title="disk full", severity="critical")
        store.save_incident(inc)
        loaded = store.get_incident(inc.id)
        assert loaded is not None
        assert loaded.title == "disk full"
        assert loaded.state == IncidentState.OPEN

    def test_list_filters_by_resource_and_state(self, store, resource):
        store.save_incident(Incident(resource_id=resource.id, title="a"))
        store.save_incident(Incident(resource_id=resource.id, title="b", state=IncidentState.RESOLVED))
        other = Resource(name="other", resource_type="server")
        store.save_resource(other)
        store.save_incident(Incident(resource_id=other.id, title="c"))

        assert len(store.list_incidents()) == 3
        assert len(store.list_incidents(resource_id=resource.id)) == 2
        assert len(store.list_incidents(state="open")) == 2
        assert len(store.list_incidents(resource_id=resource.id, state="resolved")) == 1


class TestIncidentRoutes:
    def test_create_incident(self, client, resource):
        r = client.post(
            "/api/v1/incidents",
            json={"resource_id": resource.id, "title": "high CPU", "severity": "warning"},
        )
        assert r.status_code == 200, r.text
        inc = r.json()["incident"]
        assert inc["title"] == "high CPU"
        assert inc["state"] == "open"

    def test_create_incident_missing_resource_404(self, client):
        r = client.post("/api/v1/incidents", json={"resource_id": "nope", "title": "x"})
        assert r.status_code == 404

    def test_acknowledge_transitions_and_records_note(self, client, resource, real_user):
        inc_id = client.post(
            "/api/v1/incidents",
            json={"resource_id": resource.id, "title": "x"},
        ).json()["incident"]["id"]

        r = client.post(
            f"/api/v1/incidents/{inc_id}/acknowledge",
            json={"owner_user_id": real_user.id, "note": "on it"},
        )
        assert r.status_code == 200
        inc = r.json()["incident"]
        assert inc["state"] == "acknowledged"
        assert inc["owner_user_id"] == real_user.id
        assert inc["notes"][-1]["text"] == "on it"

    def test_snooze_requires_until(self, client, resource):
        inc_id = client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "x"}).json()["incident"][
            "id"
        ]
        r = client.post(f"/api/v1/incidents/{inc_id}/snooze", json={})
        assert r.status_code == 400

    def test_snooze_with_until(self, client, resource):
        inc_id = client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "x"}).json()["incident"][
            "id"
        ]
        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        r = client.post(f"/api/v1/incidents/{inc_id}/snooze", json={"snoozed_until": until})
        assert r.status_code == 200
        assert r.json()["incident"]["state"] == "snoozed"
        assert r.json()["incident"]["snoozed_until"] is not None

    def test_resolve_sets_resolved_at(self, client, resource):
        inc_id = client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "x"}).json()["incident"][
            "id"
        ]
        r = client.post(f"/api/v1/incidents/{inc_id}/resolve", json={"note": "fixed"})
        assert r.status_code == 200
        inc = r.json()["incident"]
        assert inc["state"] == "resolved"
        assert inc["resolved_at"] is not None
        assert inc["notes"][-1]["text"] == "fixed"

    def test_assign_requires_owner(self, client, resource, real_user):
        inc_id = client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "x"}).json()["incident"][
            "id"
        ]
        r = client.post(f"/api/v1/incidents/{inc_id}/assign", json={})
        assert r.status_code == 400
        r2 = client.post(f"/api/v1/incidents/{inc_id}/assign", json={"owner_user_id": real_user.id})
        assert r2.status_code == 200
        assert r2.json()["incident"]["owner_user_id"] == real_user.id

    def test_note_endpoint_appends(self, client, resource):
        inc_id = client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "x"}).json()["incident"][
            "id"
        ]
        r = client.post(f"/api/v1/incidents/{inc_id}/note", json={"note": "first"})
        assert r.status_code == 200
        r2 = client.post(f"/api/v1/incidents/{inc_id}/note", json={"note": "second"})
        assert [n["text"] for n in r2.json()["incident"]["notes"]] == ["first", "second"]

    def test_list_endpoint(self, client, resource):
        client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "a"})
        client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "b"})
        r = client.get(f"/api/v1/incidents?resource_id={resource.id}")
        assert r.status_code == 200
        assert len(r.json()["incidents"]) == 2


class TestIncidentValidation:
    """Negative tests for the validation tightening in 0.4.5.dev0."""

    def test_create_with_invalid_severity_returns_422(self, client, resource):
        r = client.post(
            "/api/v1/incidents",
            json={"resource_id": resource.id, "title": "x", "severity": "oops"},
        )
        assert r.status_code == 422

    def test_create_accepts_each_valid_severity(self, client, resource):
        for sev in ("critical", "warning", "info"):
            r = client.post(
                "/api/v1/incidents",
                json={"resource_id": resource.id, "title": f"t-{sev}", "severity": sev},
            )
            assert r.status_code == 200, (sev, r.text)
            assert r.json()["incident"]["severity"] == sev

    def test_create_with_unknown_evaluation_id_returns_400(self, client, resource):
        r = client.post(
            "/api/v1/incidents",
            json={
                "resource_id": resource.id,
                "title": "x",
                "evaluation_id": "no-such-eval",
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "unknown_evaluation_id"

    def test_create_with_evaluation_for_other_resource_returns_400(self, client, store, resource):
        from supavision.models import Evaluation, Report, RunType, Severity

        other = Resource(name="other", resource_type="server")
        store.save_resource(other)
        rep = Report(resource_id=other.id, run_type=RunType.HEALTH_CHECK, content="x")
        store.save_report(rep)
        ev = Evaluation(
            report_id=rep.id,
            resource_id=other.id,
            severity=Severity.WARNING,
            summary="other-resource eval",
        )
        store.save_evaluation(ev)

        r = client.post(
            "/api/v1/incidents",
            json={
                "resource_id": resource.id,
                "title": "x",
                "evaluation_id": ev.id,
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "evaluation_resource_mismatch"

    def test_snooze_with_past_timestamp_returns_422(self, client, resource):
        inc_id = client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "x"}).json()["incident"][
            "id"
        ]
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        r = client.post(f"/api/v1/incidents/{inc_id}/snooze", json={"snoozed_until": past})
        assert r.status_code == 422

    def test_assign_with_unknown_owner_returns_400(self, client, resource):
        inc_id = client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "x"}).json()["incident"][
            "id"
        ]
        r = client.post(
            f"/api/v1/incidents/{inc_id}/assign",
            json={"owner_user_id": "ghost-user-does-not-exist"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "unknown_owner_user_id"

    def test_acknowledge_with_unknown_owner_returns_400(self, client, resource):
        inc_id = client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "x"}).json()["incident"][
            "id"
        ]
        r = client.post(
            f"/api/v1/incidents/{inc_id}/acknowledge",
            json={"owner_user_id": "ghost-user"},
        )
        assert r.status_code == 400
