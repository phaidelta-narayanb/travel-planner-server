from pydantic import Field, model_validator

from models import PlanEvent, PlannerData, Trip


class LLMTrip(Trip):
    description: str = Field(
        default="",
        description="Natural-language summary of the trip, its destination, dates, and relevant note.",
    )

    @model_validator(mode="after")
    def populate_description(self) -> "LLMTrip":
        if not self.description.strip():
            description = f"{self.name} is a trip to {self.destination} from {self.startDate} through {self.endDate}."
            if self.note:
                description += f" Trip note: {self.note}"
            self.description = description
        return self


class LLMPlanEvent(PlanEvent):
    description: str = Field(
        default="",
        description="Natural-language summary of the itinerary item, including timing and location when provided.",
    )

    @model_validator(mode="after")
    def populate_description(self) -> "LLMPlanEvent":
        if not self.description.strip():
            event_type = self.type.replace("_", " ")
            description = f"{event_type.capitalize()}: {self.title}, starting {self.start}"
            if self.end:
                description += f" and ending {self.end}"
            if self.location:
                description += f" at {self.location}"
            if self.note:
                description += f". Note: {self.note}"
            self.description = description + "."
        return self


class LLMPlannerData(PlannerData):
    trips: list[LLMTrip]
    events: list[LLMPlanEvent]
    description: str = Field(
        default="",
        description="Natural-language overview of this user's collection of trips and itinerary items.",
    )

    @model_validator(mode="after")
    def populate_description(self) -> "LLMPlannerData":
        if not self.description.strip():
            trip_label = "trip" if len(self.trips) == 1 else "trips"
            event_label = "itinerary item" if len(self.events) == 1 else "itinerary items"
            self.description = (
                f"This planner contains {len(self.trips)} {trip_label} and "
                f"{len(self.events)} {event_label}."
            )
        return self
