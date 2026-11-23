from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


DateOnly = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
DateTimeLocal = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$"),
]


class Trip(BaseModel):
    """A trip as represented in the frontend's local planner data."""

    id: str = Field(min_length=1, description="Stable identifier for the trip.")
    name: str = Field(min_length=1, description="User-facing name of the trip.")
    destination: str = Field(min_length=1, description="Trip destination.")
    startDate: DateOnly = Field(description="First trip date, formatted as YYYY-MM-DD.")
    endDate: DateOnly = Field(description="Last trip date, formatted as YYYY-MM-DD.")
    color: str = Field(min_length=1, description="Hex or CSS color used to identify the trip.")
    image: str = Field(description="Cover image URL.")
    note: str | None = Field(default=None, description="Optional note about the trip.")

    @field_validator("startDate", "endDate")
    @classmethod
    def validate_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_date_range(self) -> "Trip":
        if self.endDate < self.startDate:
            raise ValueError("endDate must be on or after startDate")
        return self


class PlanEvent(BaseModel):
    """An event, task, stay, or place within a trip itinerary."""

    id: str = Field(min_length=1, description="Stable identifier for the event.")
    tripId: str = Field(description="Trip identifier, or an empty string when unassigned.")
    title: str = Field(min_length=1, description="Short name of the itinerary item.")
    type: Literal["flight", "stay", "activity", "task", "place"]
    start: DateTimeLocal = Field(description="Local start date and time in the UI's datetime-local format.")
    end: DateTimeLocal | None = Field(default=None, description="Optional local end date and time.")
    location: str | None = Field(default=None, description="Optional location or address.")
    note: str | None = Field(default=None, description="Optional note or booking details.")

    @field_validator("start", "end")
    @classmethod
    def validate_datetime(cls, value: str | None) -> str | None:
        if value is not None:
            datetime.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> "PlanEvent":
        if self.end is not None and datetime.fromisoformat(self.end) < datetime.fromisoformat(self.start):
            raise ValueError("end must be on or after start")
        return self


class PlannerData(BaseModel):
    """The complete offline snapshot shape used by the UI and sync database."""

    trips: list[Trip]
    events: list[PlanEvent]


class DestinationPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    departure_region: str | None = Field(default=None, max_length=120)
    destination: str | None = Field(default=None, max_length=160)
    start_date: DateOnly | None = None
    end_date: DateOnly | None = None
    budget: Literal["budget", "moderate", "premium", "luxury", "flexible"] | None = None
    interests: list[str] = Field(default_factory=list, max_length=10)
    travel_pace: Literal["relaxed", "balanced", "packed"] | None = None
    travel_party: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator("interests")
    @classmethod
    def validate_interests(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 80 for value in values):
            raise ValueError("Interests must be non-empty and at most 80 characters")
        return values

    @model_validator(mode="after")
    def validate_date_range(self) -> "DestinationPreferences":
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("Provide both start_date and end_date, or neither")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


ItineraryFocus = Literal["activities", "sightseeing", "transport", "stays", "food", "shopping"]


class DestinationSuggestion(BaseModel):
    trip_name: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=160)
    overview: str = Field(min_length=1, max_length=1000)
    why_it_fits: str = Field(min_length=1, max_length=1000)
    seasonal_fit: str = Field(min_length=1, max_length=800)
    ideal_duration_days: int = Field(ge=1, le=30)
    recommended_focus_areas: list[ItineraryFocus] = Field(max_length=6)


class DestinationSuggestionsResponse(BaseModel):
    summary: str
    current_date: DateOnly
    suggestions: list[DestinationSuggestion] = Field(min_length=1, max_length=5)
    notice: str


class ItineraryProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip: Trip
    events: list[PlanEvent] = Field(max_length=200)
    departure_location: str | None = Field(default=None, max_length=160)
    focus_areas: list[ItineraryFocus] = Field(default_factory=list, max_length=6)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_event_trip_ids(self) -> "ItineraryProposalRequest":
        if any(event.tripId != self.trip.id for event in self.events):
            raise ValueError("Every event must belong to the requested trip")
        return self


class ProposedChange(BaseModel):
    change_id: str
    action: Literal["add", "update", "remove"]
    event_id: str | None
    event: PlanEvent | None
    summary: str
    rationale: str


class ItineraryProposalResponse(BaseModel):
    summary: str
    changes: list[ProposedChange] = Field(max_length=30)
    notice: str


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version: int = Field(ge=0)
    data: PlannerData


class HealthResponse(BaseModel):
    status: Literal["ok"]


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    email: EmailStr
    version: int = Field(ge=0)
    snapshot: PlannerData | None
    created_at: float | None


class CurrentUserResponse(BaseModel):
    email: EmailStr
    version: int = Field(ge=0)
    snapshot: PlannerData | None
    created_at: float | None


class LogoutResponse(BaseModel):
    status: Literal["signed out"]


class LatestSnapshotResponse(BaseModel):
    version: int = Field(ge=0)
    data: PlannerData | None
    created_at: float | None


class SnapshotVersionResponse(BaseModel):
    version: int = Field(ge=1)
    created_at: float


class SnapshotResponse(SnapshotVersionResponse):
    data: PlannerData


class SyncResponse(SnapshotVersionResponse):
    pass


class VersionConflictDetail(BaseModel):
    message: str
    current_version: int = Field(ge=0)
