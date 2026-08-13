from __future__ import annotations

from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import infer_model
from pydantic_ai.providers import infer_provider_class

# 0.2.0 accepted a non-standard ``gemini:`` prefix where Pydantic-AI's provider
# name is ``google``. Normalising here keeps that spelling working; every other
# prefix is Pydantic-AI's own vocabulary.
_PREFIX_ALIASES = {"gemini": "google"}


def build_model(model: str, *, api_key: str | None = None, provider: Any = None) -> Any:
    """Build a Pydantic-AI model from a ``"provider:name"`` string and explicit key.

    Prefix resolution is delegated to Pydantic-AI's ``infer_model``, with a
    ``provider_factory`` that injects credentials rather than letting it read the
    environment. A ``provider`` instance takes precedence and is used as-is, so
    it may carry a custom ``base_url`` or client; otherwise ``api_key`` goes to
    the prefix's default ``Provider`` class. Every provider Pydantic-AI knows
    therefore works with no table to maintain here, and a bare model name it can
    map to a provider is accepted too.

    Raises:
        ImproperlyConfigured: The provider could not be resolved — an unknown or
            uninferable prefix, or its extra is not installed. Pass a
            ``Provider`` instance for anything Pydantic-AI cannot infer.
    """
    prefix, sep, name = model.partition(":")
    if sep and prefix in _PREFIX_ALIASES:
        model = f"{_PREFIX_ALIASES[prefix]}:{name}"
    try:
        if provider is not None:
            return infer_model(model, provider_factory=lambda _name: provider)
        # Cast because ``infer_provider_class`` returns ``type[Provider]``, whose
        # base ``__init__`` is argument-less in the stubs while every subclass
        # accepts ``api_key``.
        return infer_model(
            model,
            provider_factory=lambda name: cast("Any", infer_provider_class(name))(api_key=api_key),
        )
    except (UserError, ValueError, ImportError) as error:
        raise ImproperlyConfigured(
            f"django-pydantic-agent: could not build model {model!r} with the supplied "
            f"api_key / provider ({error}). The model must be a 'provider:name' "
            "string (e.g. 'anthropic:claude-sonnet-4.6') whose provider "
            "Pydantic-AI knows, with the matching provider extra installed — or "
            "pass provider=YourProvider() to AGUIServer.",
        ) from error


__all__ = ["build_model"]
