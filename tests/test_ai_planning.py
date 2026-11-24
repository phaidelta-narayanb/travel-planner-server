import os
import json
from datetime import date
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi.testclient import TestClient

import ai_service
import main
from llm_models import LLMItineraryProposal, LLMProposedChange
from models import (
    DestinationSuggestion,
    DestinationSuggestionsResponse,
    ItineraryProposalRequest,
    PlanEvent,
    Trip,
)

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
main.Base.metadata.create_all(test_engine)
TestSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)


def override_get_db():
    with TestSessionLocal() as db:
        yield db


def example_trip() -> Trip:
    return Trip(
        id="trip-1",
        name="A few days away",
        destination="Kyoto, Japan",
        startDate="2027-04-10",
        endDate="2027-04-13",
        color="#729884",
        image="",
    )


def ai_query_records() -> list[main.AIQuery]:
    with TestSessionLocal() as db:
        return list(db.scalars(select(main.AIQuery)).all())


def test_ai_destination_endpoint_requires_authentication() -> None:
    with TestClient(main.app) as client:
        response = client.post("/api/ai/destinations", json={})
    assert response.status_code == 401


def test_authenticated_destination_endpoint_returns_typed_suggestions(monkeypatch) -> None:
    expected = DestinationSuggestionsResponse(
        summary="A mix of city breaks and quiet nature.",
        current_date="2027-01-01",
        suggestions=[
            DestinationSuggestion(
                trip_name=f"Trip idea {index}",
                destination=f"Place {index}",
                overview="A relaxed destination.",
                why_it_fits="It suits the requested pace.",
                seasonal_fit="Conditions are typically mild at this time.",
                ideal_duration_days=5,
                recommended_focus_areas=["activities", "sightseeing", "food"],
            )
            for index in range(1, 6)
        ],
        notice="Confirm current details before booking.",
    )
    monkeypatch.setattr(main, "suggest_destinations", lambda preferences, current_date: expected)
    previous_ids = {record.id for record in ai_query_records()}
    main.app.dependency_overrides[main.get_db] = override_get_db
    main.app.dependency_overrides[main.current_user] = lambda: main.User(
        id=1,
        email="planner@example.com",
        password_hash="",
    )
    try:
        with TestClient(main.app) as client:
            response = client.post("/api/ai/destinations", json={})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(response.json()["suggestions"]) == 5
    assert response.json()["suggestions"][0]["destination"] == "Place 1"
    [record] = [item for item in ai_query_records() if item.id not in previous_ids]
    assert record.user_id == 1
    assert record.query_type == "destinations"
    assert record.status == "succeeded"
    assert record.request_data["preferences"]["departure_region"] is None
    assert record.request_data["preferences"]["interests"] == []
    assert record.request_data["current_date"] == date.today().isoformat()
    assert record.response_data["suggestions"][0]["destination"] == "Place 1"
    assert record.completed_at is not None


def test_itinerary_endpoint_requires_authentication() -> None:
    trip = example_trip()
    payload = {
        "trip": trip.model_dump(mode="json"),
        "events": [],
        "focus_areas": ["activities"],
    }
    with TestClient(main.app) as client:
        response = client.post("/api/ai/itinerary", json=payload)
    assert response.status_code == 401


def test_authenticated_destination_endpoint_reports_missing_server_key(monkeypatch) -> None:
    def missing_key():
        raise ai_service.AIConfigurationError("AI planning is not configured.")

    monkeypatch.setattr(ai_service, "_client", missing_key)
    previous_ids = {record.id for record in ai_query_records()}
    main.app.dependency_overrides[main.get_db] = override_get_db
    main.app.dependency_overrides[main.current_user] = lambda: main.User(
        id=1,
        email="planner@example.com",
        password_hash="",
    )
    try:
        with TestClient(main.app) as client:
            response = client.post("/api/ai/destinations", json={})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == "AI planning is not configured."
    [record] = [item for item in ai_query_records() if item.id not in previous_ids]
    assert record.query_type == "destinations"
    assert record.status == "failed"
    assert record.error == "AI planning is not configured."
    assert record.response_data is None


def test_authenticated_itinerary_endpoint_logs_request_and_response(monkeypatch) -> None:
    trip = example_trip()
    payload = {
        "trip": trip.model_dump(mode="json"),
        "events": [],
        "focus_areas": ["activities"],
        "notes": "Prefer a relaxed day.",
    }
    expected = main.ItineraryProposalResponse(
        summary="A relaxed plan.",
        changes=[],
        notice="Confirm current details before booking.",
    )
    monkeypatch.setattr(main, "propose_itinerary", lambda request: expected)
    previous_ids = {record.id for record in ai_query_records()}
    main.app.dependency_overrides[main.get_db] = override_get_db
    main.app.dependency_overrides[main.current_user] = lambda: main.User(
        id=1,
        email="planner@example.com",
        password_hash="",
    )
    try:
        with TestClient(main.app) as client:
            response = client.post("/api/ai/itinerary", json=payload)
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    [record] = [item for item in ai_query_records() if item.id not in previous_ids]
    assert record.user_id == 1
    assert record.query_type == "itinerary"
    assert record.status == "succeeded"
    assert record.request_data["trip"]["id"] == trip.id
    assert record.request_data["notes"] == "Prefer a relaxed day."
    assert record.response_data["summary"] == "A relaxed plan."


def test_itinerary_proposals_bind_added_and_updated_events_to_the_trip(monkeypatch) -> None:
    trip = example_trip()
    existing = PlanEvent(
        id="event-1",
        tripId=trip.id,
        title="Visit the market",
        type="activity",
        start="2027-04-11T09:00",
    )
    replacement = existing.model_copy(update={"title": "Explore Nishiki Market"})
    added = PlanEvent(
        id="model-generated-id",
        tripId="other-trip",
        title="Tea tasting",
        type="activity",
        start="2027-04-12T14:00",
    )
    proposal = LLMItineraryProposal(
        summary="Two focused ideas for the trip.",
        changes=[
            LLMProposedChange(
                action="update",
                event_id=existing.id,
                event=replacement,
                summary="Clarify the market visit.",
                rationale="Use the specific location.",
            ),
            LLMProposedChange(
                action="add",
                event=added,
                summary="Add a tea tasting.",
                rationale="It fits the available afternoon.",
            ),
        ],
    )
    fake_client = SimpleNamespace(
        responses=SimpleNamespace(
            parse=lambda **kwargs: SimpleNamespace(output_parsed=proposal),
        ),
    )
    monkeypatch.setattr(ai_service, "_client", lambda: fake_client)
    request = ItineraryProposalRequest(trip=trip, events=[existing], focus_areas=["activities"])

    response = ai_service.propose_itinerary(request)

    update, addition = response.changes
    assert update.event_id == existing.id
    assert update.event is not None and update.event.id == existing.id
    assert addition.event is not None and addition.event.id != "model-generated-id"
    assert addition.event.tripId == trip.id


def test_itinerary_rejects_a_proposal_for_an_unknown_event(monkeypatch) -> None:
    trip = example_trip()
    proposal = LLMItineraryProposal(
        summary="An invalid suggestion.",
        changes=[
            LLMProposedChange(
                action="remove",
                event_id="not-in-the-trip",
                summary="Remove an event.",
                rationale="It is no longer needed.",
            ),
        ],
    )
    fake_client = SimpleNamespace(
        responses=SimpleNamespace(
            parse=lambda **kwargs: SimpleNamespace(output_parsed=proposal),
        ),
    )
    monkeypatch.setattr(ai_service, "_client", lambda: fake_client)
    request = ItineraryProposalRequest(trip=trip, events=[])

    with pytest.raises(ai_service.AIOutputError, match="not in this itinerary"):
        ai_service.propose_itinerary(request)


def test_same_origin_and_destination_suppresses_flight_suggestions(monkeypatch) -> None:
    trip = example_trip().model_copy(update={"destination": "Goa, India"})
    flight = PlanEvent(
        id="generated-flight",
        tripId=trip.id,
        title="Flight arrival in Goa",
        type="flight",
        start="2027-04-10T09:00",
    )
    proposal = LLMItineraryProposal(
        summary="A proposed trip plan.",
        changes=[
            LLMProposedChange(
                action="add",
                event=flight,
                summary="Add an arrival flight.",
                rationale="Travel to the destination.",
            ),
        ],
    )
    fake_client = SimpleNamespace(
        responses=SimpleNamespace(
            parse=lambda **kwargs: SimpleNamespace(output_parsed=proposal),
        ),
    )
    monkeypatch.setattr(ai_service, "_client", lambda: fake_client)
    request = ItineraryProposalRequest(
        trip=trip,
        events=[],
        departure_location="goa india",
        focus_areas=["transport"],
    )

    response = ai_service.propose_itinerary(request)

    assert response.changes == []


def test_destination_service_sets_date_from_server_context(monkeypatch) -> None:
    from llm_models import LLMDestinationSuggestions

    parsed = LLMDestinationSuggestions(
        summary="Five ideas for the coming season.",
        suggestions=[
            DestinationSuggestion(
                trip_name=f"Trip idea {index}",
                destination=f"Place {index}",
                overview="A relaxed destination.",
                why_it_fits="It suits the requested pace.",
                seasonal_fit="Conditions are typically mild at this time.",
                ideal_duration_days=5,
                recommended_focus_areas=["activities", "sightseeing", "food"],
            )
            for index in range(1, 6)
        ],
    )
    captured_request = {}

    def parse_request(**kwargs):
        captured_request.update(kwargs)
        return SimpleNamespace(output_parsed=parsed)

    fake_client = SimpleNamespace(
        responses=SimpleNamespace(
            parse=parse_request,
        ),
    )
    monkeypatch.setattr(ai_service, "_client", lambda: fake_client)

    result = ai_service.suggest_destinations(
        preferences=main.DestinationPreferences(
            departure_region="Seattle",
            destination="Japan",
        ),
        current_date=date(2027, 1, 1),
    )

    assert result.current_date == "2027-01-01"
    assert len(result.suggestions) == 5
    supplied_preferences = json.loads(captured_request["input"][1]["content"])["preferences"]
    assert supplied_preferences["departure_region"] == "Seattle"
    assert supplied_preferences["destination"] == "Japan"
