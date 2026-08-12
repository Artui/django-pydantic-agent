from __future__ import annotations

from django.db import models


class StoredConversation(models.Model):
    """The reference durable conversation row for a model-backed store.

    One row per ``(owner_id, thread_id)``. ``messages`` holds the AG-UI message
    list as JSON (the same shape every store round-trips); ``title`` and
    ``preview`` are denormalised so the thread drawer's list query never loads
    message bodies, and ``updated_at`` orders it. ``owner_id`` is the resolved
    owner (the user's pk, or an ``anon:<session_key>`` bucket under
    ``ALLOW_ANONYMOUS``) — the store always filters by it, the security boundary.

    ``attachments`` is the reconciled set of files this conversation refers to,
    derived from ``messages`` on every save (see
    :func:`~django_pydantic_agent.contrib.store.reconcile_conversation_attachments.reconcile_conversation_attachments`)
    and authoritative for attachment lifecycle: deleting the conversation drops
    the attachments nothing else still points at.

    Used by
    :class:`~django_pydantic_agent.contrib.store.default_conversation_store.DefaultConversationStore`.
    Run lineage (``run_id`` / step ledger) is intentionally out of scope here;
    a future durability layer can extend the schema.
    """

    thread_id = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    preview = models.TextField(blank=True, default="")
    messages = models.JSONField(default=list)
    attachments = models.ManyToManyField(
        "StoredAttachment",
        through="ConversationAttachment",
        related_name="conversations",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner_id", "thread_id"],
                name="django_pydantic_agent_store_owner_thread_unique",
            )
        ]
        indexes = [models.Index(fields=["owner_id", "-updated_at"])]

    def __str__(self) -> str:
        return self.title or self.thread_id


class StoredAttachment(models.Model):
    """The reference durable attachment row for a model-backed store.

    The opaque ``attachment_id`` is the handle the wire ref carries; ``file``
    holds the bytes via Django ``Storage`` (so S3 etc. come free through
    ``STORAGES``/``DEFAULT_FILE_STORAGE``), while ``name`` / ``mime`` / ``size``
    are the denormalised metadata returned without reading the file back.
    ``owner_id`` is the resolved owner (the user's pk, or an ``anon:<session_key>``
    bucket under ``ALLOW_ANONYMOUS``) — the store always filters by it, the
    security boundary. ``thread_id`` is a loose label a subclass or a project may
    set to tie an attachment to one conversation; the reference store leaves it
    blank, and it is *not* what lifecycle runs on — the
    :class:`ConversationAttachment` relation is. It is kept because projects read
    it.

    ``sha256`` is the hex digest of the file's bytes, computed chunked at upload.
    It is what makes a re-upload of the same bytes reuse the blob already in
    storage instead of writing a second copy. It is blank on rows written before
    the column existed; the ``agent_store_backfill_hashes`` management command
    fills those in.

    Used by
    :class:`~django_pydantic_agent.contrib.store.default_attachment_store.DefaultAttachmentStore`.
    """

    attachment_id = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=255, blank=True, default="")
    thread_id = models.CharField(max_length=255, blank=True, default="")
    name = models.CharField(max_length=255, blank=True, default="")
    mime = models.CharField(max_length=255, blank=True, default="")
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    # Indexed because deleting a row has to ask whether any other row still
    # points at the same stored blob before removing the bytes.
    file = models.FileField(upload_to="django_pydantic_agent/attachments/", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner_id", "attachment_id"],
                name="django_pydantic_agent_attachment_owner_id_unique",
            )
        ]
        indexes = [
            models.Index(fields=["owner_id", "-created_at"]),
            # Owner first, and only ever queried with an owner. A plain index on
            # ``sha256`` alone would make exactly the query this store must never
            # run — "who else holds these bytes?" across tenants — the cheap one.
            models.Index(fields=["owner_id", "sha256"]),
        ]

    def __str__(self) -> str:
        return self.name or self.attachment_id


class ConversationAttachment(models.Model):
    """One conversation's reference to one attachment.

    The through model behind ``StoredConversation.attachments``. It is a
    many-to-many rather than a foreign key on the attachment because both ends
    are plural: deduplication means several rows may describe the same bytes,
    and the same attachment id may be quoted by more than one thread once a user
    forwards a file into a second conversation.

    Rows are derived, never hand-written: a conversation's save reconciles them
    from the attachment ids carried by its own messages. Both sides cascade, so
    deleting either end drops the link; the attachment row itself outlives the
    link and is collected separately, once nothing references it.
    """

    conversation = models.ForeignKey(
        StoredConversation, on_delete=models.CASCADE, related_name="attachment_links"
    )
    attachment = models.ForeignKey(
        StoredAttachment, on_delete=models.CASCADE, related_name="conversation_links"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "attachment"],
                name="django_pydantic_agent_conversation_attachment_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.conversation.thread_id}:{self.attachment.attachment_id}"


# --- Durable run lineage -----------------------------------------------------
#
# The four rows below back a model-backed step store for the
# ``pydantic-ai-harness`` step-persistence capability — the durable, owner-scoped
# equivalent of the harness's own ``SqliteStepStore`` / ``FileStepStore``. Each
# mirrors one harness dataclass (``RunRecord`` / ``StepEvent`` /
# ``ContinuableSnapshot`` / ``ToolEffectRecord``) with an added ``owner_id`` — the
# resolved owner (the user's pk, or an ``anon:<session_key>`` bucket under
# ``ALLOW_ANONYMOUS``) that every query filters by, the security boundary the
# harness types themselves do not carry. Genuinely-optional lineage strings are
# ``null=True`` so a ``None`` sentinel round-trips exactly (``list_runs`` filters
# on it). Backed by
# :class:`~django_pydantic_agent.contrib.store.default_step_store.DefaultStepStore`.


class StoredRun(models.Model):
    """Lineage metadata for one agent run — the durable :class:`RunRecord`.

    One row per ``(owner_id, run_id)``. ``conversation_id`` groups the runs of a
    dialogue; ``parent_run_id`` is the hierarchical link (which run spawned this
    one) — two independent axes, so a delegated run may share a conversation
    across attempts while pointing at a different orchestrator run. ``started_at``
    is the harness-supplied run-start instant (persisted verbatim, not stamped at
    insert), and orders :meth:`list_runs`.
    """

    run_id = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=255, blank=True, default="")
    conversation_id = models.CharField(max_length=255, null=True, blank=True, default=None)
    parent_run_id = models.CharField(max_length=255, null=True, blank=True, default=None)
    agent_name = models.CharField(max_length=255, null=True, blank=True, default=None)
    metadata = models.JSONField(default=dict)
    started_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner_id", "run_id"], name="django_pydantic_agent_run_owner_run_unique"
            )
        ]
        indexes = [models.Index(fields=["owner_id", "started_at"])]

    def __str__(self) -> str:
        return self.run_id


class StoredStepEvent(models.Model):
    """One append-only :class:`StepEvent` at a run/model/tool boundary.

    Never mutated: a correction is a follow-up row, and :meth:`list_events`
    returns them in insertion order (by ``id``). ``kind`` is one of the harness
    ``EventKind`` literals; ``error`` carries ``repr(exc)`` on the ``*_failed``
    kinds. Scoped and read by ``(owner_id, run_id)``.
    """

    run_id = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=255, blank=True, default="")
    kind = models.CharField(max_length=32)
    step_index = models.IntegerField()
    timestamp = models.DateTimeField()
    conversation_id = models.CharField(max_length=255, null=True, blank=True, default=None)
    parent_run_id = models.CharField(max_length=255, null=True, blank=True, default=None)
    agent_name = models.CharField(max_length=255, null=True, blank=True, default=None)
    tool_call_id = models.CharField(max_length=255, null=True, blank=True, default=None)
    tool_name = models.CharField(max_length=255, null=True, blank=True, default=None)
    error = models.TextField(null=True, blank=True, default=None)
    metadata = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=["owner_id", "run_id"])]

    def __str__(self) -> str:
        return f"{self.run_id}:{self.kind}"


class StoredSnapshot(models.Model):
    """A provider-valid :class:`ContinuableSnapshot` safe to resume from.

    ``messages`` is the full ``list[ModelMessage]`` serialised with
    ``ModelMessagesTypeAdapter`` (JSON) — pass it to
    ``Agent.run(message_history=...)`` to continue or fork. :meth:`latest_snapshot`
    returns the most recent by **insertion order** (largest ``id``), not by
    ``step_index``, matching the harness stores. Scoped by ``(owner_id, run_id)``.

    ``state`` mirrors the harness's ``SnapshotState``. A ``complete`` snapshot
    sits at a boundary where every tool call has a matching return, so it is
    always safe to resume from; an ``interrupted`` one is a rescue point captured
    mid-tool-cycle (a crash), where pending calls may be re-executed or closed out
    with synthesized returns. ``latest_snapshot`` skips ``interrupted`` rows
    unless asked for them, which is why the state has to be *stored* rather than
    inferred — by the time a resume is attempted, the run that produced the row
    is long gone.
    """

    run_id = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=255, blank=True, default="")
    step_index = models.IntegerField()
    messages = models.JSONField(default=list)
    conversation_id = models.CharField(max_length=255, null=True, blank=True, default=None)
    parent_run_id = models.CharField(max_length=255, null=True, blank=True, default=None)
    agent_name = models.CharField(max_length=255, null=True, blank=True, default=None)
    timestamp = models.DateTimeField()
    state = models.CharField(
        max_length=16,
        default="complete",
        choices=[("complete", "complete"), ("interrupted", "interrupted")],
    )

    class Meta:
        indexes = [models.Index(fields=["owner_id", "run_id"])]

    def __str__(self) -> str:
        return f"{self.run_id}@{self.step_index}"


class StoredToolEffect(models.Model):
    """A tool call's side-effect status — the durable :class:`ToolEffectRecord`.

    Upserted on ``(owner_id, run_id, tool_call_id)`` as the call moves
    ``started`` → ``completed`` / ``failed``. A record still ``started`` after a
    process restart means the external side effect may or may not have landed
    (``unknown_after_crash``); ``idempotency_key`` / ``effect_summary`` let an
    orchestrator decide whether replay is safe. :meth:`list_unresolved_tool_effects`
    returns the ``started`` rows for a run.
    """

    run_id = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=255, blank=True, default="")
    tool_call_id = models.CharField(max_length=255)
    tool_name = models.CharField(max_length=255)
    status = models.CharField(max_length=16)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True, default=None)
    idempotency_key = models.CharField(max_length=255, null=True, blank=True, default=None)
    effect_summary = models.TextField(null=True, blank=True, default=None)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner_id", "run_id", "tool_call_id"],
                name="django_pydantic_agent_tool_effect_owner_run_call_unique",
            )
        ]
        indexes = [models.Index(fields=["owner_id", "run_id"])]

    def __str__(self) -> str:
        return f"{self.run_id}:{self.tool_call_id}:{self.status}"
