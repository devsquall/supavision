"""Tests for the Incident lightweight-state feature (P2 #12)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from supavision.db import Store
from supavision.models import Incident, IncidentState, Resource
from supavision.web.auth import generate_api_key
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

    def test_acknowledge_transitions_and_records_note(self, client, resource):
        inc_id = client.post(
            "/api/v1/incidents",
            json={"resource_id": resource.id, "title": "x"},
        ).json()["incident"]["id"]

        r = client.post(
            f"/api/v1/incidents/{inc_id}/acknowledge",
            json={"owner_user_id": "u1", "note": "on it"},
        )
        assert r.status_code == 200
        inc = r.json()["incident"]
        assert inc["state"] == "acknowledged"
        assert inc["owner_user_id"] == "u1"
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

    def test_assign_requires_owner(self, client, resource):
        inc_id = client.post("/api/v1/incidents", json={"resource_id": resource.id, "title": "x"}).json()["incident"][
            "id"
        ]
        r = client.post(f"/api/v1/incidents/{inc_id}/assign", json={})
        assert r.status_code == 400
        r2 = client.post(f"/api/v1/incidents/{inc_id}/assign", json={"owner_user_id": "u2"})
        assert r2.status_code == 200
        assert r2.json()["incident"]["owner_user_id"] == "u2"

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
