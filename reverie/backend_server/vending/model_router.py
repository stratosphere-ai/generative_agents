"""Per-persona model routing.

Vendy runs on the main model (default `gpt-4o`); customer personas run on a
cheaper model (default `gpt-4o-mini`). Routing is implemented as a
monkey-patch on `openai.ChatCompletion.create` so the upstream
`gpt_structure.py` is left untouched.

Usage at sim start (called from reverie.py):

    from vending.model_router import install, with_persona

    install()                          # patch openai once

    for persona_name, persona in personas.items():
        with with_persona(persona):
            persona.move(...)          # all openai calls inside use the routed model

If the openai module is not available (e.g. unit tests), `install()` is a no-op.
"""

from __future__ import annotations

import contextlib
import functools
import os
import threading
from typing import Any, Iterator


_current = threading.local()


def _role_for(persona: Any) -> str:
    role = getattr(getattr(persona, "scratch", None), "role", None) or getattr(persona, "role", None)
    if role:
        return role
    if getattr(persona, "vending_state", None) is not None:
        return "vendor"
    return "vendor"


def model_for_role(role: str) -> str:
    if role == "customer":
        return os.environ.get("MODEL_CUSTOMER", "gpt-4o-mini")
    return os.environ.get("MODEL_VENDY", "gpt-4o")


@contextlib.contextmanager
def with_persona(persona: Any) -> Iterator[None]:
    prev = getattr(_current, "role", None)
    _current.role = _role_for(persona)
    try:
        yield
    finally:
        if prev is None:
            try:
                del _current.role
            except AttributeError:
                pass
        else:
            _current.role = prev


def current_model() -> str | None:
    role = getattr(_current, "role", None)
    if role is None:
        return None
    return model_for_role(role)


_INSTALLED = False


def install() -> bool:
    """Patch openai.ChatCompletion.create to honor the current persona's role.

    Returns True if patching succeeded, False if openai is unavailable.
    Safe to call multiple times (idempotent).
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        import openai                                            # type: ignore[import-not-found]
    except ImportError:
        return False

    chat_create = getattr(getattr(openai, "ChatCompletion", None), "create", None)
    if chat_create is None:
        return False

    @functools.wraps(chat_create)
    def routed_create(*args: Any, **kwargs: Any):
        override = current_model()
        if override is not None:
            kwargs["model"] = override
        return chat_create(*args, **kwargs)

    openai.ChatCompletion.create = routed_create
    _INSTALLED = True
    return True
