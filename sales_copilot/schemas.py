"""Strict tool contracts and the application-created approval envelope."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SearchLeads(StrictModel):
    city: str | None = Field(default=None, max_length=100)
    status: Literal["new", "contacted", "qualified", "won", "lost"] | None = None
    owner_id: int | None = Field(default=None, gt=0)
    unassigned_only: bool = Field(default=False, description="True to select only leads without an owner. owner_id=null alone means no owner filter.")
    sort_by: Literal["id", "score", "business"] = Field(default="id", description="score ranks by recorded numerical score; business uses trusted host weights. id preserves ordinary search order.")
    sort_order: Literal["asc", "desc"] | None = Field(default=None, description="Default: asc for id, desc for score/business. Default ranking ties use recorded priority descending then ID ascending; host configuration may disable the priority tie-break.")

    @model_validator(mode="after")
    def compatible_owner_filters(self):
        if self.unassigned_only and self.owner_id is not None:
            raise ValueError("unassigned_only cannot be combined with an owner_id")
        return self


class GetLeadDetails(StrictModel):
    lead_id: int = Field(gt=0)


class AssignLead(StrictModel):
    lead_id: int = Field(gt=0)
    assignee_id: int = Field(gt=0)
    request_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")


class RequestIntent(StrictModel):
    """Untrusted extraction of the user's request, independent of CRM tool calls."""

    operation_type: Literal["read", "first_assignment", "clarification"]
    lead_id: int | None = Field(default=None, gt=0, description="Explicit lead ID, including the subject of a read. An ID alone is never an assignment request.")
    assignee_id: int | None = Field(default=None, gt=0, description="Explicit agent ID if mentioned. Only first_assignment registers an action; read IDs grant no write capability.")


class AssignmentIntent(StrictModel):
    """A task to resolve, never an authorization or consent capability."""

    operation_type: Literal["first_assignment"] = "first_assignment"
    lead_id: int = Field(gt=0)
    assignee_id: int = Field(gt=0)
    operation_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")

    def action(self) -> dict:
        return AssignLead(lead_id=self.lead_id, assignee_id=self.assignee_id,
                          request_key=self.operation_id).model_dump()


class RetrievePolicy(StrictModel):
    query: str = Field(min_length=3, max_length=1000)
    top_k: int = Field(default=3, ge=1, le=5)


class ApprovalDecision(StrictModel):
    approved: StrictBool


class BoundApprovalDecision(ApprovalDecision):
    actor_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    action_fingerprint: str = Field(min_length=64, max_length=64)
    challenge_id: str = Field(min_length=1)


class ApprovedAssignment(StrictModel):
    """Graph envelope. The backend additionally verifies its signed consent capability.

    Constructing this model does not grant authorization or approval.
    Host dispatch has no native tool-call ID. Provenance is bookkeeping; the
    signed backend consent still binds actor, exact action and operation.
    """

    approved: Literal[True]
    action: AssignLead
    source: Literal["host", "model"] = "model"
    tool_call_id: str | None = Field(default=None, min_length=1)
    consent: dict | None = None

    @model_validator(mode="after")
    def truthful_call_provenance(self):
        if (self.source == "host" and self.tool_call_id is not None
                or self.source == "model" and self.tool_call_id is None):
            raise ValueError("Approval provenance must match an actual native call, or host dispatch without one")
        return self

    @field_validator("approved", mode="before")
    @classmethod
    def explicit_true_only(cls, value):
        if value is not True:
            raise ValueError("Only explicit boolean True is approved")
        return value
