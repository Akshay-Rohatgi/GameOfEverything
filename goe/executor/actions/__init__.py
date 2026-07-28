from dataclasses import dataclass, field


@dataclass
class ActionResult:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    status_code: int | None = None
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    evaluate_return: str | None = None
    extracted_value: str | None = None
    current_url: str = ""
    error: str | None = None
