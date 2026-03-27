from typing import Optional, List
from pydantic import BaseModel
from game_of_everything.models import (
    ParsedRequest, MappedRequest, MappedAtom, GeneratedSnippet, TestResult,
)


class GoEState(BaseModel):
    raw_request: Optional[str] = None
    parsed_request: Optional[ParsedRequest] = None
    mapped_request: Optional[MappedRequest] = None
    sequenced_request: Optional[List[MappedAtom]] = None
    generated_snippets: Optional[List[GeneratedSnippet]] = None
    test_results: Optional[List[TestResult]] = None
    final_script: Optional[str] = None
