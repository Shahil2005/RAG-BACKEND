"""Lightweight intent router (LangGraph-style v1 without full graph dependency)."""

from __future__ import annotations

import re
from typing import Literal

Intent = Literal["mail", "documents", "workspace", "general"]


def classify_intent(query: str) -> Intent:
    q = query.strip().lower()
    if re.search(r"\b(email|mail|inbox|outlook|message|sender|unread)\b", q):
        return "mail"
    if re.search(r"\b(sharepoint|onedrive|document|file|folder|drive)\b", q):
        return "documents"
    if re.search(r"\b(workspace|sales|operations|restoration)\b", q):
        return "workspace"
    return "general"


def suggest_sources(intent: Intent) -> list[str]:
    if intent == "mail":
        return ["outlook"]
    if intent == "documents":
        return ["sharepoint", "onedrive"]
    if intent == "workspace":
        return ["outlook", "sharepoint", "onedrive"]
    return ["outlook", "sharepoint", "onedrive"]
