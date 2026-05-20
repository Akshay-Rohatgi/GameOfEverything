from __future__ import annotations

from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Action types (discriminated union on "type")
# ---------------------------------------------------------------------------

class HttpRequestAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["http_request"]
    method: str
    url: str
    headers: dict[str, str] = {}
    body: str | None = None


class ExecAttackerAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["exec_attacker"]
    command: str


class ExecTargetAction(BaseModel):
    """God-view only — valid in L1 diagnostics, never in L2 procedures."""
    model_config = ConfigDict(strict=True)
    type: Literal["exec_target"]
    command: str


class ListenAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["listen"]
    port: int
    duration: int


class SleepAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["sleep"]
    seconds: int


class NavigateAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["navigate"]
    path: str


class ClickAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["click"]
    selector: str


class FillAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["fill"]
    fields: dict[str, str]


class FillAndSubmitAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["fill_and_submit"]
    fields: dict[str, str]
    submit: str | None = None


class EvaluateAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["evaluate"]
    script: str


class WaitCondition(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["selector", "url", "network_idle", "text_visible"]
    value: str | None = None


class WaitForAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["wait_for"]
    condition: WaitCondition


class UploadAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["upload"]
    selector: str
    filename: str
    file_content: str


class ExtractAction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["extract"]
    selector: str
    attribute: str


Action = Annotated[
    Union[
        HttpRequestAction,
        ExecAttackerAction,
        ExecTargetAction,
        ListenAction,
        SleepAction,
        NavigateAction,
        ClickAction,
        FillAction,
        FillAndSubmitAction,
        EvaluateAction,
        WaitForAction,
        UploadAction,
        ExtractAction,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Assertion types (tagged union — NOT discriminated, checked by key presence)
# ---------------------------------------------------------------------------

class StatusAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    status: int


class ExitCodeAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    exit_code: int


class StdoutContainsAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    stdout_contains: str


class StdoutRegexAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    stdout_regex: str


class ReceivedContainsAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    received_contains: str


class ReceivedRegexAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    received_regex: str


class BodyContainsAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    body_contains: str


class BodyRegexAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    body_regex: str


class SelectorVisibleAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    selector_visible: str


class SelectorNotVisibleAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    selector_not_visible: str


class SelectorTextAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    selector: str
    contains: str


class SelectorCountAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    selector: str
    count: int


class UrlContainsAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    url_contains: str


class UrlEqualsAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    url_equals: str


class CookieExistsAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    cookie_exists: str


class CookieValueAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str
    contains: str


class LocalStorageContainsAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    key: str
    contains: str


class EvaluateResultContainsAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    evaluate_result_contains: str | None


class EvaluateResultRegexAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    evaluate_result_regex: str


class ExtractedContainsAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    extracted_contains: str


class ExtractedRegexAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    extracted_regex: str


class TitleContainsAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    title_contains: str


# AllAssertion uses forward reference resolved below
class AllAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    all: list[Assertion]  # type: ignore[name-defined]


Assertion = Union[
    AllAssertion,
    StatusAssertion,
    ExitCodeAssertion,
    StdoutContainsAssertion,
    StdoutRegexAssertion,
    ReceivedContainsAssertion,
    ReceivedRegexAssertion,
    BodyContainsAssertion,
    BodyRegexAssertion,
    SelectorVisibleAssertion,
    SelectorNotVisibleAssertion,
    SelectorTextAssertion,
    SelectorCountAssertion,
    UrlContainsAssertion,
    UrlEqualsAssertion,
    CookieExistsAssertion,
    CookieValueAssertion,
    LocalStorageContainsAssertion,
    EvaluateResultContainsAssertion,
    EvaluateResultRegexAssertion,
    ExtractedContainsAssertion,
    ExtractedRegexAssertion,
    TitleContainsAssertion,
]

# Resolve forward reference in AllAssertion
AllAssertion.model_rebuild()


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class SessionAuth(BaseModel):
    model_config = ConfigDict(strict=True)
    login_url: str
    username_field: str
    password_field: str
    username: str
    password: str
    success_indicator: str


class Session(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str
    type: Literal["browser"] = "browser"
    base_url: str
    auth: SessionAuth | None = None


# ---------------------------------------------------------------------------
# Step + Procedure
# ---------------------------------------------------------------------------

class Step(BaseModel):
    model_config = ConfigDict(strict=True)
    step_id: str
    session: str | None = None
    action: Action
    expect: Assertion | None = None
    outputs: dict[str, str] = {}
    timeout: int = 10


class Procedure(BaseModel):
    model_config = ConfigDict(strict=True)
    sessions: list[Session] = []
    procedure: list[Step]
