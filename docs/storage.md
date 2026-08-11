# Storage

Storage here is **contracts plus reference implementations**. The protocols are
core because every transport needs the same persistence semantics; the HTTP
*views* over them are not — a browser thread drawer and an agent-to-agent peer
model the same history completely differently.

## The contracts

Two `Protocol`s, both `runtime_checkable`, both fully async so an implementation
can use the async ORM or a network backend:

- **`ConversationStore`** — `load` / `save` / `delete` a conversation, plus
  listing metadata for a history drawer.
- **`AttachmentStore`** — `save` / `open` / `delete` files a user attached.

You pass an instance to your transport; nothing is resolved from a dotted path.

## Owner scoping is the security boundary

Both contracts are **owner-scoped**, and this is the part to get right in a
custom implementation: a store filters by the acting user so one user can never
read or delete another's data.

`AttachmentStore.open` returns `None` for a missing **or cross-owner** id rather
than raising, so a caller maps both to a 404 — a cross-owner id must not be
distinguishable from a missing one.

`AttachmentStore.save` deliberately validates nothing about size or type. That
is the calling view's job, from its own config; the store just persists bytes
and returns a durable `AttachmentRef`.

## The `read_attachment` tool

The bytes never travel on the wire. A transport that has an attachment store
composes `build_attachment_toolset(store, request)` into the agent, and the
model reaches a file server-side, by id, only when it asks — through the same
owner-scoped `open` a download view would use.

Reading one has three outcomes:

| The attachment | What the model gets |
| --- | --- |
| Textual (`text/*`, JSON, XML, JavaScript, YAML, SVG) and valid UTF-8 | Its text, inline. |
| A type providers can read — PDF, PNG, JPEG, GIF, WebP — within the size cap | The bytes, as attached file content it can look at directly. |
| Anything else: an unreadable type, an oversized file, undecodable "text" | A one-line note with the name, type and size. |

Tune the middle row with `AttachmentInlineConfig`:

```python
from django_pydantic_agent import AttachmentInlineConfig
from django_pydantic_agent.agent.attachment_toolset import build_attachment_toolset

toolset = build_attachment_toolset(
    store,
    request,
    inline=AttachmentInlineConfig(max_bytes=1024 * 1024),
)
```

**The type list is an allowlist rather than "everything that is not text", and
that is on purpose.** Bytes a provider cannot interpret are worse than useless:
a `.zip` or a `.exe` handed over as file content makes the provider reject the
whole request, so a broad rule would trade a model that cannot read your PDF
for a run that does not start.

**The size cap is much smaller than any upload cap you would set**, and for a
different reason. An inlined file is carried in a synthetic `user` message that
the tool return serialises into — so it is persisted into the stored
conversation, shipped to the browser on every thread load, and re-sent by the
client on every following turn. Base64 adds about a third: a 4 MiB PDF costs
roughly 5.5 MiB in the conversation row. That round trip is what lets a
*follow-up* question about the same file be answered without a second
`read_attachment` call, so it buys something real — but it is why the default
sits at 4 MiB rather than at whatever your upload endpoint accepts.

To keep the old behaviour and never attach bytes:

```python
toolset = build_attachment_toolset(
    store,
    request,
    inline=AttachmentInlineConfig(media_types=frozenset()),
)
```

`AttachmentRef.mime` is client-declared, so the decision is made on a hint. The
failure mode of a mislabelled file is a provider rejecting the request, not a
disclosure — the store is owner-scoped either way.

## Threads key by `(owner_id, thread_id)`

Which has a consequence worth knowing before you mount two endpoints: **two
transports sharing one store share one user's thread list.** A conversation
started at `/internal/agent` shows up in `/public/agent`'s history and can be
resumed there — under the *public* agent's model, tools and guard policy.

`ScopedConversationStore` partitions them without a migration:

```python
from django_pydantic_agent import ScopedConversationStore

internal = ScopedConversationStore(store, scope="internal")
public = ScopedConversationStore(store, scope="public")
```

## What ships

| Implementation | Use it when |
| --- | --- |
| `NullConversationStore` | The default. `load` returns `None`, `save`/`delete` no-op — the server stays stateless and the conversation lives in the client's posted history. A transport treats this as "persistence off". |
| `NullAttachmentStore` | The default. Uploads off. |
| `DjangoSessionConversationStore` | Per-user persistence with **no migration** — conversations live in the Django session. Good for a demo or a small deployment. |
| `ModelConversationStore` / `ModelAttachmentStore` | Abstract model-backed bases to subclass against your own models. |
| `ScopedConversationStore` | Wraps any of the above to partition by scope. |

## The reference models

The base package ships **no model of its own**, so projects that don't opt in
get no migration. To use the ready-made ones:

```python
INSTALLED_APPS = [
    ...,
    "django_pydantic_agent.contrib.store",
]
```

Then `migrate`, and pass the matching store to your transport
(`conversation_store=` / `attachment_store=` / `step_store=`). The app provides
`DefaultConversationStore`, `DefaultAttachmentStore` and `DefaultStepStore`.

`DefaultAttachmentStore` keeps bytes in Django `Storage` and metadata in a row,
so S3 and friends come free through `STORAGES` — you don't configure anything
here beyond your normal Django storage backend.

## Step persistence

`DefaultStepStore` is the durable, owner-scoped equivalent of
`pydantic-ai-harness`'s own `SqliteStepStore` / `FileStepStore`. Note the
direction of the contract: it **structurally satisfies harness's `StepStore`
protocol** — that protocol is upstream's, not this package's, and this package
does not redeclare it. Owner scoping is entirely ours; no harness record carries
one, which is what makes the store multi-tenant rather than a single-user
ledger.

It needs the `[harness]` extra, since the protocol it satisfies lives there.

Full signatures in the [persistence reference](reference/persistence.md).
