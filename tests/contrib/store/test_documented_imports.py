"""The import lines ``docs/storage.md`` teaches, executed.

The page used to name the three stores with no path at all, one line under an
``INSTALLED_APPS`` snippet, which reads as an invitation to
``from django_pydantic_agent.contrib.store import DefaultConversationStore`` — a
spelling that raises. django-ag-ui's own configuration page had it right, which is
why nobody noticed: the wrong page was the one a newcomer reaches first.

Asserted by **reading the page and running what it shows**, rather than by
importing the modules this test knows about. A test that imports the right thing
passes just as happily while the docs teach the wrong thing, which is the failure
this is here to prevent.
"""

from __future__ import annotations

import pathlib
import re

import pytest

DOCS = pathlib.Path(__file__).resolve().parents[3] / "docs" / "storage.md"

STORES = (
    "DefaultConversationStore",
    "DefaultAttachmentStore",
    "DefaultStepStore",
    "DefaultMemoryStore",
)

# An import statement inside a fenced Python block, over one line or several.
_IMPORT = re.compile(
    r"^from\s+(django_pydantic_agent[\w.]*)\s+import\s+\(?\s*([\w,\s]+?)\s*\)?$",
    re.MULTILINE,
)


def _python_blocks(markdown: str) -> list[str]:
    return re.findall(r"^```python\n(.*?)^```", markdown, re.MULTILINE | re.DOTALL)


def _documented_imports() -> dict[str, str]:
    """Every ``name -> module`` pair the page's Python blocks import from us."""
    found: dict[str, str] = {}
    for block in _python_blocks(DOCS.read_text()):
        # Join a parenthesised import's continuation lines before matching.
        flattened = re.sub(r"\(\s*\n\s*", "(", block).replace(",\n)", ")")
        for module, names in _IMPORT.findall(flattened):
            for name in (part.strip() for part in names.split(",")):
                if name:
                    found[name] = module
    return found


def test_the_page_shows_an_import_for_each_reference_store() -> None:
    documented = _documented_imports()

    assert set(STORES) <= documented.keys(), (
        f"docs/storage.md names {STORES} but shows no import for "
        f"{sorted(set(STORES) - documented.keys())}"
    )


@pytest.mark.parametrize("store", STORES)
def test_the_import_the_page_shows_actually_works(store: str) -> None:
    module_path = _documented_imports()[store]

    module = __import__(module_path, fromlist=[store])

    assert isinstance(getattr(module, store), type)


@pytest.mark.parametrize("store", STORES)
def test_the_page_does_not_teach_the_package_level_import(store: str) -> None:
    """The shorter path cannot exist, and this pins the reason it stays that way.

    A re-export in ``contrib/store/__init__.py`` would import models while Django
    builds the app registry, raising ``AppRegistryNotReady`` at startup for every
    project that installs the app. If someone adds one, this fails and the package
    docstring explains why.

    ``exec`` rather than ``importlib``, because the failure being pinned belongs to
    the *statement* a reader would write: reaching the attribute through importlib
    raises ``AttributeError``, which is not what anybody sees.
    """
    assert _documented_imports()[store] != "django_pydantic_agent.contrib.store"

    with pytest.raises(ImportError):
        exec(f"from django_pydantic_agent.contrib.store import {store}", {})
