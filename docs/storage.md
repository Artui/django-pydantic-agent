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
different reason: it bounds what one *request* carries. An inlined file rides in
a synthetic `user` message that the tool return serialises into, and that
message stays in the run's history, so every further model request in the same
run ships the file again. Base64 adds about a third — a 4 MiB PDF is roughly
5.5 MiB of provider payload each time, in tokens, in latency, and in the memory
the run holds it in.

It does not cost the conversation. The bytes never travel on the event stream,
so the client never receives them and the history it posts on the next turn
carries none; whether they outlive the run is the transport's decision, and the
AG-UI transport strips them on the way to storage. A *follow-up* question about
the same file is answered by reading the attachment again, server-side. That is
why the default sits at 4 MiB rather than at whatever your upload endpoint
accepts.

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

Everything from here down is the **reference implementation's** behaviour, not
the contract. `ConversationStore` and `AttachmentStore` stay id-based, the wire
is unchanged, and a session-backed or S3-only store you plug in yourself owes
none of this.

## The same file, uploaded twice

`StoredAttachment` carries a `sha256` of the file's bytes, hashed in chunks at
upload so a large file is never held in memory whole just to fingerprint it.
When an upload matches one the same owner already has, the new row points at the
blob already in storage instead of writing a second copy.

**A row per upload, even on a hit.** The row holds the `name` the composer shows
on the chip, so the same bytes sent again as `contract-final.pdf` keep that name
rather than inheriting the first upload's. What is shared is the file
underneath; the bytes are removed only when the last row pointing at them goes.

**Deduplication never crosses owners, and that is not an oversight.** Sharing one
copy of a document held by a hundred tenants would be the better trade on
storage alone. It is not available: a cross-owner hit tells you another tenant
holds a file whose bytes you already have, which is how you confirm that a named
party is a customer, or where a leaked document came from. The lookup is scoped
to one owner, and the index on the column leads with `owner_id` so the
cross-tenant question is not even the cheap one to ask.

Rows written before the column existed have no hash, so they cannot be matched
until `agent_store_backfill_hashes` fills them in.

## Attachment lifecycle

An attachment is reachable as long as a conversation refers to it, and the
reference is derived, not declared. Saving a conversation reconciles the
`StoredConversation.attachments` relation from the attachment ids its own
messages carry — the web component already puts them there, on the user message,
as an `attachments` array of the refs it uploaded. No wire change and no client
change: the ids are in the payload either way.

The parse is total. A malformed entry degrades to "no reference" rather than
raising inside a save, and an id is resolved only against its own owner's
attachments, so a guessed or copied id links nothing.

Two consequences:

- **Deleting a conversation deletes the attachments nothing else refers to**, and
  their bytes. One quoted by a second thread survives untouched.
- **An upload that was never sent belongs to no conversation**, so no cascade
  will ever reach it. `agent_store_prune_attachments` is what collects those.

`StoredAttachment.thread_id` is untouched by all of this and stays. It is a loose
label a project may set; the relation, not the column, is what lifecycle runs on.

**Nothing is ever deleted on a timer the library set up.** Deletion happens on a
cascade you can point at, or on a command you schedule. There is no TTL sweep.

Deleting `StoredConversation` rows by another route — a queryset `delete()`, the
admin — skips the cascade and leaves the attachments unreferenced. They are not
stranded permanently; the prune command is exactly what collects them.

## Maintenance commands

The `contrib.store` app ships three, all of them opt-in and none of them
scheduled for you:

| Command | What it does |
| --- | --- |
| `agent_store_backfill_hashes` | Hash attachments stored before the `sha256` column existed, so they can take part in deduplication. A missing blob is a reported skip, not a failure. |
| `agent_store_prune_attachments` | Delete unreferenced attachments older than `--older-than` (default `24h`). |
| `agent_store_strip_inline_bytes` | Rewrite stored conversations to drop inlined base64 file content. |

All three take `--dry-run`.

**`--older-than` is why the prune command is safe to run.** References alone
cannot tell an upload abandoned last month from one sitting in a composer right
now — both have zero. The threshold is the floor on how long an upload must
survive before it counts as abandoned, so set it to comfortably more than the
longest a message may sit unsent in your product:

```bash
python manage.py agent_store_prune_attachments --older-than 7d --dry-run
```

### Reclaiming space from threads that inlined files

A transport that hands the model an attachment inline serialises the file into
the message list as base64, and the message list is persisted: a 2.6 MB PDF costs
roughly 3.5 MB in one row, loaded whole every time the thread is opened.
Transports have stopped writing them and strip them on the way in, but a row
already written keeps its payload until something rewrites it:

```bash
python manage.py agent_store_strip_inline_bytes --dry-run
python manage.py agent_store_strip_inline_bytes
```

It edits the JSON structurally rather than round-tripping it through a message
type, because that round trip is exactly what would drop each message's `id` and
the non-standard `attachments` array — the array that renders the chips and that
the attachment relation is reconciled from. The bytes themselves are not lost:
the attachment is untouched in the attachment store, and the model reaches it the
way it always does, by id.

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
