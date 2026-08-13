# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation

- **The storage page now shows how to import the reference stores.** It named
  `DefaultConversationStore`, `DefaultAttachmentStore` and `DefaultStepStore` one
  line under an `INSTALLED_APPS` snippet with no path at all, which reads as an
  invitation to `from django_pydantic_agent.contrib.store import
  DefaultConversationStore` — a spelling that raises. The page shows the leaf-module
  imports, and says why the shorter one cannot exist: this package is a Django *app*,
  so `INSTALLED_APPS` imports it while the app registry is being built, and a
  re-export there would import models at that moment and raise `AppRegistryNotReady`
  on startup for every project that installs it, whether or not it uses a store.
  `contrib/store/__init__.py` carries the same explanation, so the next reader does
  not "fix" the emptiness. Found by following the page from outside the package while
  building the framework gallery; django-ag-ui's configuration page had it right,
  which is why it went unnoticed — the wrong page is the one a newcomer reaches
  first.

  Held to that spelling by a test that **reads the page and runs what it shows**,
  rather than importing the modules the test already knows about: a test that imports
  the right thing passes just as happily while the docs teach the wrong thing.

### Fixed

- **The reST literal-block marker no longer reaches the page.** Sphinx reads a
  trailing `::` as "an indented literal block follows" and prints one colon;
  Markdown has no such rule, so the second colon rendered verbatim (`Example::`).
  The indented block was already coming out as a highlighted code block either
  way, so this drops the stray character and nothing else.

### Fixed

- **Docstring cross-references now render as links instead of raw markup.** The
  docstrings carried Sphinx roles — ``:class:`~django_pydantic_agent.AttachmentStore` ``
  — but the docs build is mkdocstrings, which renders docstring bodies as
  Markdown and has no such syntax. Every one of them reached the published page
  verbatim, `:class:` prefix and Sphinx's abbreviating `~` included. They are now
  mkdocstrings autorefs links (`` [`AttachmentStore`][django_pydantic_agent.AttachmentStore] ``),
  so the reference cross-links instead of printing its own markup.

  References to symbols the reference does not render — contrib models, private
  methods, internal helpers — and to third-party symbols became plain code spans
  rather than dead links; no inventory for external packages is configured, so a
  link to one could not resolve.

## [0.15.0] — 2026-08-12

### Added

- **The reference attachment store deduplicates uploads by content hash, within
  one owner.** `StoredAttachment` gains a `sha256` column, hashed in chunks at
  upload so a 10 MiB file is fingerprinted without being held in memory whole.
  An upload whose bytes the same owner already has points at the blob already in
  storage instead of writing a second copy — the same file dropped into five
  threads was five blobs before this.

  A row is still created per upload, deliberately. The row carries the `name`
  the composer renders on the chip, so the same bytes sent again under a
  different filename must keep their own name; what is shared is the file
  underneath.

  **Deduplication is scoped to one owner and stays that way.** Sharing a copy
  across tenants is the better trade on storage alone, and it is not on offer: a
  cross-owner hit discloses that another tenant holds a file whose bytes you
  already have. The new index leads with `owner_id` for the same reason.

  Rows written before the column existed have no hash and cannot be matched
  until the new `agent_store_backfill_hashes` command fills them in — a command
  rather than a data migration, because hashing means reading every blob back
  out of storage and a deploy should not be held open for a bucket.

- **Attachments now have a lifecycle: a conversation delete takes the ones
  nothing else refers to.** A new `ConversationAttachment` through model links
  conversations to attachments, and saving a conversation reconciles it from the
  attachment ids the messages already carry (the `attachments` array the web
  component rides on a user message). No wire change and no client change. The
  parse is total — a malformed entry degrades to "no reference" rather than
  raising mid-save — and ids resolve only within their own owner, so a guessed
  id links nothing.

  Deleting a conversation through the store then deletes the attachments no
  other conversation references, blobs included; one a second thread quotes
  survives untouched. Deleting rows by another route (a queryset `delete()`, the
  admin) skips the cascade and leaves them unreferenced for the prune command.

- **`agent_store_prune_attachments`, for uploads that were never sent.** Those
  belong to no conversation, so no cascade ever reaches them. It takes
  `--older-than` (default `24h`), and the threshold is the point: references
  alone cannot tell an upload abandoned last month from one sitting in a
  composer right now, since both have zero, and a naive sweep would delete a file
  out from under a user mid-compose. `--dry-run` reports what would go, counting
  a shared blob only when every row pointing at it is in the set.

  The library still deletes on nothing but a cascade you can point at and a
  command you schedule. There is no TTL sweep and no timer.

- **`agent_store_strip_inline_bytes`, to reclaim space from threads that inlined
  files.** A transport that hands the model an attachment inline serialises it
  into the message list as base64; a 2.6 MB PDF costs roughly 3.5 MB in one row,
  shipped to the browser on every load. Transports have stopped writing them,
  but rows already written keep their payload until something rewrites them.

  It edits the JSON structurally and never round-trips it through a message
  type, because that round trip is what drops each message's `id` and the
  non-standard `attachments` array — the array that renders the chips and that
  the new relation is reconciled from. `--dry-run` reports the bytes it would
  reclaim. The attachment itself is untouched; the model still reaches it by id.

### Changed

- **Deleting an attachment no longer deletes its bytes unconditionally.** With
  deduplication two rows can share one stored file, and the old unconditional
  `file.delete()` would have left the survivor resolving to nothing. The bytes
  now go when the last row pointing at them does.

- `StoredAttachment.thread_id` is unchanged and stays. It is a loose label a
  project may set; the new relation, not the column, is what lifecycle runs on.

- **One migration comes with all of this** —
  `0003_attachment_content_hash_and_conversation_links`. Projects that opted
  into `django_pydantic_agent.contrib.store` need to run `migrate`; projects
  that did not still get no model and no migration.

### Fixed

- **A PDF or an image the user attached came back to the model as a note saying
  "its content is not text and was not inlined", so the agent asked for a file
  it had already been given.** The user drops a PDF into the composer, asks
  about the budget in it, and the answer is "please attach the PDF here" — the
  model called `read_attachment` correctly and still never saw the file.
  `read_attachment` now returns the bytes as attached file content for the
  types providers can actually read: PDF, PNG, JPEG, GIF and WebP.

  **It is an allowlist, not "everything that is not text".** A `.zip` or an
  `.exe` handed over as file content is not merely useless to the model — the
  provider rejects the request — so a broad rule would have traded a model that
  cannot read your PDF for a run that does not start. Everything outside the
  list still returns the same note it returned before.

  **`read_attachment` no longer always returns `str`.** For a PDF or image
  inside the size cap it returns a `pydantic_ai.messages.ToolReturn`. Nothing
  about the model-facing text changes for **textual** attachments — those are
  byte-identical to before — and every non-inlined case still returns the exact
  same string, but code that calls the tool function directly and assumes a
  string sees the difference.

  **Inlined bytes are paid for per request, inside the run that reads the
  file.** They ride in a synthetic `user` message that the tool return
  serialises into, and that message stays in the run's history, so every further
  model request in the same run ships the file again. Base64 adds about a third:
  a 4 MiB PDF is roughly 5.5 MiB of provider payload each time, in tokens,
  latency and the memory the run holds it in.

  They do **not** travel on the event stream. The client never receives them, so
  the history it posts on the next turn carries none, and whether they outlive
  the run at all is the transport's call — the AG-UI transport strips them on
  the way to storage. A *follow-up* question about the same file is answered by
  reading the attachment again, server-side, not from bytes replayed out of the
  stored thread. To turn inlining off entirely, pass
  `AttachmentInlineConfig(media_types=frozenset())`.

### Added

- **`AttachmentInlineConfig`, and an `inline=` keyword on
  `build_attachment_toolset`.** `media_types` is the set of content types whose
  bytes are attached rather than described; `max_bytes` (default 4 MiB, checked
  against the bytes the store returns rather than the declared
  `AttachmentRef.size`) is where a file is described instead. `inline=None`
  keeps the defaults, so an existing call site needs no edit.

  The cap sits far below any upload limit you would set, for the per-request
  reason above: the file goes to the provider on every model request left in the
  run. Raise it knowing what it costs per request.

  **The escape hatch is constructor-only for now**, because this substrate
  reads no Django settings by design. A future release of the transports will
  surface it through their own settings namespaces — that needs this package
  released first, so until then a project overriding it passes an
  `AttachmentInlineConfig` at the call site.

## [0.14.0] — 2026-08-11

### Changed

- **The upper bound came off every sibling window: `djangorestframework-mcp-server>=0.30`,
  `djangorestframework-pydantic-ai>=0.16`, `pydantic-ai-harness[code-mode]>=0.13`.**
  Each was a one-minor window over a package we ship ourselves, which is not a
  compatibility statement but a *schedule*: every upstream release made this
  package unresolvable until someone re-cut it, whether or not anything broke.
  Against that there is no recorded case of a ceiling here catching a real
  incompatibility — while they caused four incidents in this ecosystem,
  including a **Security** release published-and-unreachable, and two disjoint
  windows that resolved *successfully* by silently downgrading a consumer past
  every fix. ⇒ *A consumer can now combine this package with the current
  sibling on the day it ships.*

  **`pydantic-ai-harness` is an external 0.x package, where a minor bump is
  breaking by SemVer and we do not control the release.** That is the risky part
  of this change and it is a bet, not a proof: that the weekly drift job finds a
  breaking 0.x minor faster than a stale ceiling would have been noticed. The
  evidence behind the bet is that a stale harness ceiling has already made this
  stack unreachable twice, and neither was caught by the ceiling itself.

- **`pydantic-ai-slim` keeps its `<3`.** A major bound is a real compatibility
  statement — the v2 capability seam is what this package is built on — and
  nothing here argues for dropping it.

### Added

- **A `floor` job in `tests.yml`, wired into the `tests` aggregate gate.** It
  resolves every *declared* dependency at `--resolution lowest-direct` and runs
  the suite, then installs the package **alone** — no extras, no dev group — and
  imports it plus a few public symbols. ⇒ *The two measurements that replace the
  ceiling are now both in place: `upstream-drift.yml` resolves unpinned weekly
  (the newest end), and `floor` resolves lowest-direct per PR (the oldest end).*
  An all-extras install cannot check a floor on its own, because one extra can
  hold a shared dependency above the floor being claimed.

## [0.13.0] — 2026-08-11

### Changed

- **Upstream windows moved to the releases that let a `FilterSet` own ordering**
  — `djangorestframework-mcp-server>=0.30,<0.31` and
  `djangorestframework-pydantic-ai>=0.16,<0.17`. **The previous pins could not
  admit either**: both siblings published while this package still required
  `<0.30` / `<0.16`, so a project on the current dpa could not install the
  ordering fix at all. Ordering itself needs no change here — dpa carries the
  conventions text, and both upstreams now speak the FilterSet's vocabulary.

- **The `[harness]` window widened to `<0.19`, from a `<0.17` that had gone
  stale against a published 0.18.1.** The extra resolved to 0.16.x and could not
  combine with a project on the current harness — the third instance of
  *published-and-unreachable* in this stack, and the first spotted by a consumer
  reading our own changelog. ⇒ *The pattern is now legible from outside, which
  makes leaving one open more expensive than the bump.*

  The relock carries `pydantic-ai-slim` 2.19 → **2.27.1** with it. The suite
  passes unchanged at 100%, but that is eight minors of upstream in one step and
  worth knowing before it is bisected from.

## [0.12.1] — 2026-08-11

### Fixed

- **`pydantic-ai-slim` floor raised to `>=2.16`, because 0.11.0 and 0.12.0 do
  not import on anything older.** `ToolFailurePolicy` imports
  `pydantic_ai.exceptions.ToolFailed` at module scope, and that symbol first
  exists in **2.16.0** — while the declared floor was `>=2,<3`. Since
  `build_agent` composes the policy by default, the failure is not a missing
  feature but an `ImportError` on `import django_pydantic_agent`.

  **The floor was verified against the wrong thing.** Development and CI
  resolve the *newest* in-range pydantic-ai (2.19 at the time), so every gate
  passed; a consumer whose own constraints pulled an older 2.x got a package
  that would not import. `django-admin-agent` resolved 2.9.1 and was the first
  to hit it. ⇒ *a dependency floor is a claim about the oldest version that
  works, and a lockfile can only ever check the newest — so a new import of an
  upstream symbol is a floor change, not just a code change.*

  The excluded range never worked, so nothing that previously installed
  successfully is affected.

## [0.12.0] — 2026-08-11

### Changed

- **`[drf-mcp]` now requires `djangorestframework-mcp-server>=0.29,<0.30`**
  (was `>=0.28,<0.29`), and the dev group moves with it.

  **The previous ceiling made 0.29.0 published-and-unreachable.** Its change
  is a default rather than a fix — drf-mcp's `MAX_PAGE_SIZE` drops from 500 to
  100, so a `paginate=True` selector tool stops advertising a `limit.maximum`
  five times its own dispatch default — but the shape is the one this project
  has been bitten by twice: a release announcement reads as completion while
  every install keeps resolving the previous version.

  drf-mcp 0.29.0 and `djangorestframework-pydantic-ai` 0.15.0 both require
  `djangorestframework-services>=0.35,<0.36`, so the `[drf-mcp]` and
  `[spec-tools]` extras stay co-installable; verified by resolving the pair and
  asserting the versions, not merely that resolution succeeded.

  No code changes; the suite passes unmodified at 100% coverage.

  **A minor, and `django-ag-ui` must follow.** Its ceiling is
  `django-pydantic-agent>=0.11,<0.12`, so this release is excluded until that
  floor moves — the published-but-unreachable interval, entered deliberately
  here rather than letting a ceiling move arrive under a patch.

## [0.11.0] — 2026-08-11

### Added

- **A raising tool now fails its own call instead of the whole run.**
  `ToolFailurePolicy` is composed by `build_agent` **by default**, so an
  unhandled exception in a tool comes back to the model as a result marked
  failed, naming the tool, and the turn continues.

  The behaviour it replaces was the worse one: the exception propagated, the
  transport emitted `RUN_ERROR`, and the answer the model had already assembled
  was discarded along with the results of every other tool in the same round.
  One broken integration cost the whole turn.

  Tune it with `AgentConfig(tool_failure=ToolFailureConfig(...))` —
  `enabled=False` restores the old behaviour, `include_detail=True` puts the
  exception type and text in the model-facing message.

  **`include_detail` is off by default, and that split is the point.** Whether
  the run survives is a reliability question; whether an exception's text
  reaches the model is a disclosure one — a traceback message can carry a
  query, a path or a credential, and anything handed to the model is also
  handed to whatever renders the transcript. The operator's copy is never
  redacted: the full exception still reaches the audit logger and the
  `django_pydantic_agent.failure` logger.

  **It hangs off Pydantic-AI's `on_tool_execute_error` hook rather than
  wrapping the handler in `except Exception`, and that is a correctness
  difference rather than a stylistic one.** Pydantic-AI does not route
  control-flow exceptions to that hook — `SkipToolExecution`, `CallDeferred`,
  `ApprovalRequired`, the `ModelRetry` retry signal, or an explicit
  `ToolFailed`. A hand-written catch would have swallowed `ApprovalRequired`
  and silently disabled the destructive-tool gate.

  It re-raises as `ToolFailed`, which spends no retry budget by design. A model
  can therefore call a persistently broken tool again; bound that with run-level
  `UsageLimits`.

### Changed

- **`AgentConfig` gained `tool_failure`, and its default changes existing
  behaviour.** A run that previously died on a raising tool now completes. No
  API is removed and nothing needs editing to upgrade, but a project relying on
  the exception escaping `agent.run(...)` should set
  `tool_failure=ToolFailureConfig(enabled=False)`.

## [0.10.0] — 2026-08-10

### Changed

- **`[drf-mcp]` now requires `djangorestframework-mcp-server>=0.28,<0.29`**
  (was `>=0.27,<0.28`).

  **The previous ceiling excluded a security release.** drf-mcp 0.28.0
  refuses an authenticated caller with no `pk` instead of collapsing every such
  caller onto the shared `"anonymous"` principal — where any two of them can
  present each other's sessions. Every consumer of this extra pinned `<0.28`, so
  the fix was **published and unreachable**, which is the quiet half of that
  failure: the release announcement reads as completion while installs keep
  resolving the vulnerable version.

  The bridge reaches it directly. `DRFMCPToolset` calls `list_tools` /
  `acall_tool`, so it runs the same principal resolution the HTTP transport
  runs — this is not an HTTP-only concern.

  No code changes; the suite passes unmodified at 100% coverage.

## [0.9.0] — 2026-08-10

### Changed

- **`[spec-tools]` now requires `djangorestframework-pydantic-ai>=0.15,<0.16`**
  (was `>=0.13,<0.14`) and **`[harness]` requires
  `pydantic-ai-harness[code-mode]>=0.13,<0.17`** (was `>=0.12,<0.13`), matching
  the windows `django-ag-ui` already used.

  **They were disjoint, and the symptom was not an error.** Asking for both
  packages' extras — `django-ag-ui[spec-tools]` alongside
  `django-pydantic-agent[spec-tools]` — resolved *successfully* by silently
  **downgrading `django-ag-ui` to 0.3.0**; the `[harness]` pair downgraded it to
  0.17.0. A resolver satisfies disjoint windows by walking the consumer back to
  a version whose pins overlap, and there is no version far enough back to be
  refused. So the failure mode was an install that looked clean and shipped a
  transport from months earlier — behind every security fix since, including
  the fail-open auth transport and the closed-by-default authentication flip.

  **Neither package is wrong on its own**, which is why nothing caught it:
  each resolves fine alone, and `django-ag-ui[spec-tools]` alone is fine too,
  since this package arrives as a plain dependency with no extras. It takes
  asking for both to see it.

  **The second-order cost was quieter still.** `build_spec_capability` is the
  path *both* transports take, and this package's suite was exercising it
  against PAI 0.13 while `django-ag-ui` ran it against 0.15. The shared wrapper
  was tested against an upstream it does not meet in production.

  No code changes: the suite passes unmodified against PAI 0.15 and harness
  0.14, at 100% coverage.

  **A minor, and `django-ag-ui` must follow.** Its ceiling is
  `django-pydantic-agent>=0.8,<0.9`, so this release is *excluded* until that
  floor moves — the published-but-unreachable interval, deliberately entered
  here rather than letting a two-minor upstream jump arrive under a patch.

## [0.8.0] — 2026-08-10

### Added

- **`AgentDeps.ip_address`**, and `AuditCapability` now reads the client IP from
  the run's deps, falling back to its constructor argument.

  **This is what makes an audited agent reusable across requests.** The IP is
  per-run data; a constructor argument is per-agent. Taking it only from the
  constructor forces a transport to build a fresh agent — schemas and all — for
  every request, which is the exact closure `AgentDeps` exists to replace. And
  the failure if a transport builds once anyway is **silent**: every audit
  record carries the IP of whoever happened to arrive first, and the records
  look perfectly well-formed.

  Nothing that already worked changes. A run whose deps carry no `ip_address` —
  unset, a project's own deps class, or no deps at all — falls back to the
  constructed value, so the constructor argument remains correct for a value
  that really is fixed per endpoint (an organization scope).

## [0.7.1] — 2026-08-10

### Documentation

- **`build_tool_catalog` now states what it does not cover.** Tools reaching the
  agent through a transport's `capabilities=` / `toolsets=` — an arbitrary
  `AbstractToolset` attached directly — are not listed, and their cards fall
  back to a prettified name.

  **A stated boundary, not a gap left open.** Pydantic-AI's enumeration is
  `AbstractToolset.get_tools`, which is `async` and takes a `RunContext`; the
  catalog is built at configuration time, from a view, with no run in sight.
  Enumerating the toolsets we happen to recognise and skipping the rest would
  produce a catalog that *looks* complete and is not — the failure mode this
  stack spends its time removing, traded here for cosmetics.

  **There is a covered route for the case that matters.** A `SpecToolset` /
  `SpecCapability` passed to django-ag-ui's `service_specs=` (0.30+) is attached
  as itself *and* enumerated, so the powerful form keeps its labels.

  An unlabelled card is a degraded label, not a broken call.

## [0.7.0] — 2026-08-10

### Changed — BREAKING for `[spec-tools]`

- **`[spec-tools]` requires `djangorestframework-pydantic-ai>=0.13`** (was
  `>=0.12,<0.13`). No API changes here, but two of PAI's changes reach a
  consumer straight through `build_spec_capability`:

  **A spec with no `permission_classes` now raises `ImproperlyConfigured`
  instead of becoming an ungated tool.** Over HTTP `permission_classes=None`
  means *inherit* — the viewset's own classes, then
  `DEFAULT_PERMISSION_CLASSES` — and off HTTP neither exists. So a spec that is
  correctly guarded behind a viewset, with passing HTTP tests, was callable by
  whatever the model decided to call. **Set `spec.permission_classes` on every
  spec you expose.**

  **`build_spec_capability` deliberately does not expose PAI's
  `require_permissions=False` migration flag.** A knob threaded through here
  would reach only callers that construct the capability themselves; the right
  shape is a general "pass any `SpecToolset` option" seam, and that is not built
  yet. A project migrating a large registry can attach the capability directly
  in the meantime — `AgentConfig(capabilities=[SpecCapability(specs,
  require_permissions=False)])` — at the cost of skipping this function's
  tool-catalog registration, so tool-call cards render unlabelled. A migration
  step, not a destination.

  - **List tools advertise `ordering`, not `order`**, and only where the toolset
    declares `ordering_fields`. Docs here referred to the old name; corrected.

- **`[drf-mcp]` requires `djangorestframework-mcp-server>=0.27`** (was
  `>=0.26,<0.27`).

  **Not optional — the two extras were mutually unsatisfiable without it.**
  PAI 0.13 requires `djangorestframework-services>=0.35,<0.36` while drf-mcp
  0.26 required `>=0.34.0,<0.35`. Disjoint, so
  `django-pydantic-agent[drf-mcp,spec-tools]` **could not resolve at all**.
  drf-mcp 0.27.0 moves its window to 0.35; nothing else about it changed.

  **Two siblings pinning one upstream to single-minor windows break every time
  one moves first.** Worth knowing when scheduling the next drf-services bump:
  the follow-up release is part of the cost of moving either package, not a
  surprise.

## [0.6.1] — 2026-08-10

### Security

- **`[drf-mcp]` now requires `djangorestframework-mcp-server>=0.26`** (was
  `>=0.25,<0.26`), which closes a fail-open authentication defect. The old
  exclusive ceiling *excluded* the fix, so installing this extra resolved to the
  vulnerable release.

  **This one reaches through `DRFMCPToolset`, not only over HTTP.** The bridge
  calls `MCPServer.list_tools` / `acall_tool`, so it runs the same permission
  and listing checks the HTTP transport runs — and those were the sites that
  failed open. A project whose `MCPPermission.has_permission` or `is_listable`
  was written `async def` got an un-awaited coroutine back; a coroutine is
  truthy and is never `None`, so **every binding was listed and every call was
  granted**, to the model as much as to an HTTP client. Upstream now raises
  `ImproperlyConfigured` naming the offending class. The same shape was swept
  across the auth backend, rate limiters, and the sync transport's session
  store.

  Nothing in this package supplies those hooks, so no code changes here — but if
  your project does write one `async def`, expect a loud refusal where there was
  previously a silent yes. That is the fix working.

## [0.6.0] — 2026-08-07

### Changed

- **`[drf-mcp]` requires `djangorestframework-mcp-server>=0.25`, `[spec-tools]`
  requires `djangorestframework-pydantic-ai>=0.12`.**

  **These are floors, not merely widened ceilings, because 0.25 changes
  behaviour the bridge sits on**: a tool registered with no permissions now
  *raises* instead of warning, and a request with no `Mcp-Session-Id` returns
  `400` rather than `404`. A range that merely admitted 0.25 while a consumer
  resolved 0.24 would pair cleanly and behave differently — the pairing a
  resolver cannot see.

  **The strict-permissions change reached here immediately**: three bridge
  fixture servers registered tools without permissions and stopped importing.
  They now declare `AllowAny` explicitly, which is the honest form for a
  fixture — "deliberately open" said out loud. Consumers upgrading should expect
  the same, and `REST_FRAMEWORK_MCP['REQUIRE_TOOL_PERMISSIONS'] = False` is the
  migration escape hatch.

  This could not ship with the rest of the sweep. drf-mcp 0.25 requires
  drf-services `>=0.34` while PAI 0.11.1 required `>=0.33,<0.34`, so a project
  depending on both extras was **unsatisfiable** until PAI 0.12.0 was
  *published*. Two siblings pinning a common upstream at incompatible ranges is
  invisible to any per-package check.

## [0.5.1] — 2026-08-02

### Changed

- **Extra floors raised to `djangorestframework-mcp-server>=0.24.1` and
  `djangorestframework-pydantic-ai>=0.11.1`.**

  These are **floor** moves, not ceiling widenings — the previous ranges already
  admitted the patched releases, so nothing was unresolvable. What they did not
  do is *guarantee* them, and the versions below the new floor carry an
  authorization bypass in their transitive `djangorestframework-services`
  dependency: nested target resolution built its kwarg pool without stripping the
  reserved dispatcher seeds, so a caller-supplied `user` key outranked the
  authenticated one in the pool that decides which row gets mutated and which set
  gets bulk-deleted. Fixed in drf-services 0.33.0.

  A version pair that resolves cleanly and leaves the bypass live is exactly
  what a resolver cannot see, which is why the floor moves rather than the
  ceiling. Installing this extra now gets the fix, rather than merely permitting
  it.

  No source changes; the full suite passes against the updated chain untouched.

## [0.5.0] — 2026-07-31

### Changed

- **Ceilings raised: drf-mcp-server to `<0.25` (was `<0.22`) and
  djangorestframework-pydantic-ai to `<0.12` (was `<0.11`).** **This resolves a
  live install conflict, not just staleness:** drf-mcp 0.24.0 requires
  drf-services `>=0.32` while PAI `<0.11` required `<0.30`, so the `[drf-mcp]`
  and `[spec-tools]` extras had become mutually unsatisfiable at their new
  versions. PAI 0.11.0 moved first; this picks both up.

- **An unknown drf-mcp tool is now a `ModelRetry`, not a fatal
  `RuntimeError`** — and the cause is upstream. drf-mcp emitted `-32004` for an
  unknown tool until 0.24.0, where it moved onto **`-32602`** to match the MCP
  spec's own worked example. The bridge branched on `-32602` to mean "malformed
  arguments, let the model self-correct", so the two conditions are no longer
  distinguishable by code and one policy has to cover both.

  **Retrying is the deliberate choice.** `-32602` is by definition a fault in
  the request *the model produced* — a wrong name or wrong arguments — and both
  are things it can change on a second attempt. Ending an entire run because a
  model guessed a tool name wrong is the harsher failure and the one a user
  notices; pydantic-ai bounds the retries, so a genuinely unfixable call still
  stops the run, just later.

  **The retry now names the real tools** when the failing name was not one of
  them — a model that invented a name needs the available ones, and a bare
  "unknown tool" tells it nothing it did not already know. Malformed arguments
  keep the field-level detail instead; the message carries whichever of the two
  actually helps, never both.

  Auth, rate limits and internal faults are unchanged: still `RuntimeError`,
  because nothing the model writes changes them.

## [0.4.4] — 2026-07-30

### Changed

- **`[drf-mcp]` → `djangorestframework-mcp-server>=0.17,<0.22`**, taking in both
  0.20.0 and 0.21.0. Two consumer-reported blockers, neither of which touches
  this bridge:
  - **0.21.0** — `DjangoOAuthToolkitBackend` rejected every bearer token once a
    resource URL was configured. Audience enforcement read a `resource` field
    that DOT's `AccessToken` does not have, so it could never succeed;
    enforcement is now the separate `ENFORCE_AUDIENCE`, default off.
  - **0.20.0** — dynamically registered clients could not be issued an ID token
    (`Application.algorithm` was never set), so the token endpoint 500'd whenever
    the advertised `openid` scope was requested.

  Both are confined to drf-mcp's OAuth surface. This bridge consumes `MCPServer`
  and the tool registry in-process, so no adaptation was needed — verified with
  the lock updated to 0.21.0 and the suite green.

  0.20.0 also added `UndescribedToolWarning` for a tool registered with no
  description. Two bridge fixtures do that on purpose, to cover the
  no-description path, so the warning is filtered in `pyproject.toml` rather
  than papered over by giving fixtures descriptions they are meant to lack.

## [0.4.3] — 2026-07-29

### Changed

- **`[drf-mcp]` → `djangorestframework-mcp-server>=0.17,<0.20`**, so 0.19.0 is
  installable. That release fixes dynamic client registration, which issued
  credentials that could never authenticate: `token_endpoint_auth_method` was
  not modelled, so every registration silently became a confidential client, and
  the `client_secret` handed back was the stored PBKDF2 digest rather than the
  secret. Both are confined to drf-mcp's `contrib.oauth` — the bridge this
  extra backs consumes `MCPServer` and the tool registry, neither of which
  changed, so the widening is purely a ceiling lift and this package's own
  behaviour is unaffected.

  Verified rather than assumed: the suite runs green against 0.19.0 with the
  lock updated.

## [0.4.2] — 2026-07-29

### Changed

- **Widened the two agent-tool integration pins** so the current majors of both
  backing packages are installable:
  - `[spec-tools]` → `djangorestframework-pydantic-ai>=0.9,<0.11`. 0.10.0 adds
    `SpecToolset(host=…)`, the origin that makes DRF's `FileField` /
    `Hyperlinked*` fields render absolute URLs off the HTTP path. Nothing here
    uses it — the widening is what lets a *consumer* pass it.
  - `[drf-mcp]` → `djangorestframework-mcp-server>=0.17,<0.19`. 0.18.0 fixes two
    reported crashes: serializer-context providers called positionally
    (`TypeError` for any provider not leading with `view, request`) and the
    missing DRF baseline context (`KeyError: 'request'`). It also carries a
    deliberate break — a provider whose first two parameters are named something
    other than `view` / `request` now raises — but that is a change to *user*
    provider signatures, not to anything this package calls.

  The floors stay at `0.9` / `0.17`: neither integration uses new API, so both
  ranges are honestly satisfiable, and a project already pinned to an older
  release isn't forced forward.

## [0.4.1] — 2026-07-28

### Documentation

- **Document the `[harness]` extra** — it has existed since the CodeMode work but
  `integrations.md` only covered the two DRF bridges, so the third extra was
  undiscoverable. The new section covers the seam (`AgentConfig.capabilities`
  takes live capability instances, so a harness capability composes exactly like
  a first-party one), **compaction** for long tool-heavy runs, and **agent
  skills** for progressive disclosure.
  - Compaction is presented by **cost**, since that is the real choice:
    `SlidingWindow` / `ClearToolResults` are free and transparent,
    `SummarizingCompaction` spends a model call per compaction,
    `TieredCompaction` escalates between them.
  - Two gotchas found by running the snippets rather than reading the source:
    `SummarizingCompaction` **requires** `max_messages` or `max_tokens`, and
    `Skills(include=…/exclude=…)` **validates** the names against what discovery
    actually found — a name that matches no skill raises rather than being
    ignored.
  - Also recorded: nothing is emitted when a compaction fires, so a transport
    that wants to tell the user "earlier turns were condensed" has to observe it
    via the one-method `CompactionStrategy` protocol.

## [0.4.0] — 2026-07-28

### Added

- **New migration `0002_snapshot_state` — run `migrate` when upgrading.**
  `StoredSnapshot` gains a `state` column mirroring the harness's `SnapshotState`
  (`complete` / `interrupted`, defaulting to `complete`, so existing rows keep
  today's behaviour).

  A `complete` snapshot sits at a boundary where every tool call has a matching
  return, so resuming from it is always safe. An `interrupted` one is a rescue
  point captured mid-tool-cycle — pending calls may be re-executed or closed out
  with synthesized returns — so `latest_snapshot()` now **skips interrupted rows
  unless asked for them**. The state has to be *stored* rather than inferred:
  by the time a resume is attempted, the run that produced the row is gone.

  When several snapshots exist, the state filter applies **after** ordering, so
  the newest `complete` row wins even when newer `interrupted` rows sit above it
  — the same walk-back-from-newest the harness reference stores do.

### Changed

- **`[harness]` now requires `pydantic-ai-harness>=0.12,<0.13`** (was `>=0.7,<0.8`).
  The ceiling had gone five minors stale, which on a 0.x library is where breakage
  accumulates — and it did. It also gated
  [`pydantic_ai_harness.skills`](https://github.com/pydantic/pydantic-ai-harness/pull/396),
  which does not exist below 0.11 and is the prerequisite for adopting agent skills.
- **`DefaultStepStore.latest_snapshot()` gained an `include_interrupted`
  keyword**, matching the harness's `StepStore` protocol. Harness's own
  `continue_run()` passes it, so before this the resume path raised
  `TypeError: latest_snapshot() got an unexpected keyword argument`. **Any custom
  `StepStore` implementation must add the same parameter.**
- **Raise the drf-chain ceilings: `[drf-mcp]` → `djangorestframework-mcp-server>=0.17,<0.18`
  (was `>=0.15,<0.16`) and `[spec-tools]` → `djangorestframework-pydantic-ai>=0.9,<0.10`
  (was `>=0.8,<0.9`).** The MCP ceiling had gone stale a wave earlier — drf-mcp
  0.16.0 (MCP Apps) was already excluded — so two upstream releases were
  unreachable from here rather than one. **No adaptation was needed**, which the
  three relevant upstream changes explain:
  - **MCP Apps (drf-mcp 0.16.0)** adds `ui://` resources and `_meta.ui` links on
    tool definitions. The bridge reads `name` / `description` / `inputSchema` /
    `outputSchema` / `annotations` off `tools/list` and ignores `_meta`, so the
    addition is inert here. The resource-encoding fix in the same release (non-JSON
    resource bodies no longer come back as quoted JSON string literals) touches
    the resource surface, which this bridge does not use — it calls tools only.
  - **The shared `UrlKwarg` / `QueryParam` (drf-mcp 0.17.0, PAI 0.9.0)** are
    re-exported from `djangorestframework-services` rather than defined locally,
    behind permanently preserved import paths. Neither is imported here. PAI's
    switch from `ValueError` to `ImproperlyConfigured` for a bad channel
    declaration is likewise unreachable: `SpecCapability` is constructed with a
    spec mapping and no channel registrations.
  - **`InputRequired` enforcement (drf-services 0.28)** makes a missing
    marked-required input raise `ServiceValidationError` at dispatch. Over the
    MCP bridge that already arrives as an `isError` result with
    `type == "validation_error"`, which `call_tool` maps to `ModelRetry` — so a
    spec adopting the marker gets a model-correctable failure through this path
    with no change here.

## [0.3.0] — 2026-07-27

### Added

- **`AgentDeps` — typed, per-run dependencies threaded through the agent.**
  `build_agent` now returns `Agent[AgentDeps, Any]` (`deps_type=AgentDeps`), so
  a transport hands each run an `AgentDeps(user=request.user)` and every tool,
  toolset and capability reads request-scoped values off `RunContext.deps` —
  pydantic-ai's own seam for exactly this.
  - **The acting user binds natively.** `djangorestframework-pydantic-ai`'s
    `SpecToolset` already defaults to reading `ctx.deps.user`; until now this
    package overrode that with a closure over the request, so the upstream
    default could never fire.
  - **It unblocks reusing a built agent.** A capability that closes over a
    request can only serve that request, forcing a per-request rebuild —
    schemas and all. Request-independent collaborators are the precondition for
    building once and binding the user per run.
  - **AG-UI shared state now has somewhere to land.** `AgentDeps` satisfies
    pydantic-ai's `StateHandler` protocol, so a run's `RunAgentInput.state` is
    validated into `deps.state` rather than dropped with a `UserWarning`. Seed
    `state` with a Pydantic model instance to get it validated against that
    model. **Inbound only** — nothing emits `STATE_SNAPSHOT` / `STATE_DELTA`
    back yet; a tool must return those as `ToolReturn` metadata.
  - `AgentDeps` is the one record here that is **not frozen**: the UI adapter
    assigns `deps.state = …` directly (it does *not* use `dataclasses.replace`,
    despite what the protocol's own comment suggests). Deps are per-run and
    never shared, so the mutability is contained.

### Changed

- **`AgentFactoryFn` now returns `Agent[AgentDeps, Any]`.** A project using the
  `agent_factory=` escape hatch must build its agent with
  `deps_type=AgentDeps`; one that omits it produces an agent whose tools see no
  acting user. Type checkers flag it, which is how this surfaced.
- **`build_spec_capability(specs, request, …)` → `build_spec_capability(specs, …)`
  — the `request` argument is gone.** It existed solely to supply
  `get_user=lambda _ctx: request.user`; with typed deps, `SpecToolset`'s own
  default reads `ctx.deps.user` and the override is unnecessary. **Breaking for
  direct callers** (pre-1.0): drop the positional `request`, and make sure runs
  are given `deps=AgentDeps(user=…)`, or spec tools will act as `None`.

## [0.2.0] — 2026-07-27

### Added

- **Spec composition accepts a spec registry, not just a mapping.**
  `build_spec_capability(specs, request, …)` now takes either a
  `name -> spec` mapping or a **spec registry** — drf-services 0.27's
  `SpecRegistry`, the one declaration site for a project exposing the same specs
  over more than one transport. New `resolve_spec_mapping()` helper and a
  `SpecSource` Protocol.
  - **Matched structurally, not imported.** `SpecSource` declares only
    `specs() -> dict`, so this substrate still names no drf-services type and
    still depends on `pydantic-ai-slim` alone — drf-services arrives only with
    the optional `[spec-tools]` extra. Naming `SpecRegistry` in a signature
    would either force the dependency on every install (including projects whose
    tools are plain `@tool` functions) or bury the type behind a lazy import
    where it cannot appear in a signature at all. drf-services duck-types
    `SelectorSpec.filter_set` for the same reason.
  - **`resolve_spec_mapping()` is public on purpose.** A transport needs the
    same normalisation *before* the builder runs: iterating a registry yields
    `RegisteredSpec` records, not names, so a transport reserving tool names by
    iterating the raw argument would fill its collision-detection set with
    dataclasses and silently stop detecting duplicates between the `@tool`
    registry, the drf-mcp bridge and the spec tools.

### Changed

- **`[spec-tools]` now requires `djangorestframework-pydantic-ai>=0.8,<0.9`**
  (was `>=0.5,<0.6`) — registry support landed in PAI 0.8.
- **`[drf-mcp]` now requires `djangorestframework-mcp-server>=0.15,<0.16`**
  (was `>=0.12,<0.13`). This is **not** optional housekeeping: drf-mcp 0.12 caps
  drf-services at `<0.26` while PAI 0.8 requires `>=0.27`, so moving only
  `[spec-tools]` makes the two extras **mutually uninstallable**. They have to
  advance together.

### Documentation

- **A real documentation site.** The package shipped in 0.1.0 with 36 exported
  symbols, a one-paragraph `docs/index.md` and a nav containing only "Home" — no
  reference at all. It now has five narrative pages (Concepts, Tools, Storage,
  Policy, Integrations) and a five-page autodoc reference covering the whole
  public surface, plus a README that says what the package does rather than only
  what it is. Highlights the things the source knows but nothing surfaced:
  capabilities compose order-independently; `category` is advisory while
  `destructive` drives the gate; the `x-*` schema keys reach the *client* while
  `DESTRUCTIVE_METADATA_KEY` is read *server-side*; stores key on
  `(owner_id, thread_id)`, so two transports sharing one store share a user's
  thread list unless wrapped in `ScopedConversationStore`.
- **Corrected a claim that this package declares a `StepStore` protocol.** It
  does not — `DefaultStepStore` structurally satisfies **`pydantic-ai-harness`'s**
  protocol, which is upstream's. The repo conventions asserted otherwise in two
  places.

### Fixed

- **The spec-conventions test asserted behaviour that changed in PAI 0.6.0.**
  `SpecCapability.get_instructions()` has returned `None` since the conventions
  moved onto `SpecToolset.get_instructions()` (so they reach the model whether a
  toolset is attached directly or wrapped, and are collected exactly once). The
  stale `[spec-tools]` ceiling pinned this package below 0.6, so the assertion
  kept passing against an API two minors old; raising the pin surfaced it. The
  test now reads the instructions off the toolset, and
  `build_spec_capability`'s module docstring no longer claims the capability
  emits them.

## [0.1.0] — 2026-07-23

### Added

- **Initial extraction** of the settings-agnostic agent-host substrate from
  `django-ag-ui`: `AgentConfig` + `build_agent`, the `ToolRegistry` / `@tool`
  registry and typed schema derivation, toolset & capability composition
  (including the optional `[drf-mcp]` bridge and `[spec-tools]` capability), the
  `AuditLogger` protocol and audit capability, the `ToolGuard` policy, the
  `get_user` / authorization helpers, the `ConversationStore` / `AttachmentStore`
  / `StepStore` contracts, and the reference `contrib.store` models and stores.
- **The core reads no Django settings.** Anything that previously resolved from
  `DJANGO_AG_UI` is now an explicit argument — notably the model stores take
  `allow_anonymous: bool = False` rather than consulting a settings key.
- **`Conversation.messages` is transport-owned.** The core persists and returns
  JSON-serialisable message records verbatim and never interprets them, so it
  carries no dependency on any wire format; the calling transport validates its
  own shape (and its message ids survive a round trip untouched).

[Unreleased]: https://github.com/Artui/django-pydantic-agent/compare/v0.15.0...HEAD
[0.15.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.12.1...v0.13.0
[0.12.1]: https://github.com/Artui/django-pydantic-agent/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/Artui/django-pydantic-agent/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Artui/django-pydantic-agent/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/Artui/django-pydantic-agent/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.4.4...v0.5.0
[0.4.4]: https://github.com/Artui/django-pydantic-agent/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/Artui/django-pydantic-agent/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/Artui/django-pydantic-agent/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Artui/django-pydantic-agent/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Artui/django-pydantic-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Artui/django-pydantic-agent/releases/tag/v0.1.0
