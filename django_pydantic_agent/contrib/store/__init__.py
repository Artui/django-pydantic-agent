"""The reference model-backed stores, each imported from its own module.

Deliberately empty of re-exports, and it has to stay that way. This package is a
Django **app** — projects list ``django_pydantic_agent.contrib.store`` in
``INSTALLED_APPS`` — so Django imports it while building the app registry. Every
store here reaches models, so re-exporting one would import models at that moment
and raise ``AppRegistryNotReady`` on startup for every project that installs the
app, whether or not it ever touches a store.

So the import path is the leaf module::

    from django_pydantic_agent.contrib.store.default_conversation_store import (
        DefaultConversationStore,
    )

``tests/contrib/store/test_documented_imports.py`` holds the docs to that spelling,
because the shorter one is what everybody tries first.
"""
