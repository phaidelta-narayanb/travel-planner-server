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
