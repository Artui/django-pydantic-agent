from __future__ import annotations

from django.db import models

from django_pydantic_agent.contrib.store.attachment_storage import attachment_storage


class StoredConversation(models.Model):
    """The reference durable conversation row, one per ``(owner_id, thread_id)``.

    ``owner_id`` is the resolved owner (the user's pk, or an
    ``anon:<session_key>`` bucket) and every query filters by it: it is the
    security boundary. ``title`` / ``preview`` are denormalised so the thread
    drawer's list query never loads message bodies. ``attachments`` is derived
    from ``messages`` on every save and is what attachment lifecycle runs on.

    Used by ``DefaultConversationStore``.
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

    ``attachment_id`` is the opaque handle the wire ref carries; ``file`` holds
    the bytes via Django ``Storage``; ``name`` / ``mime`` / ``size`` are
    denormalised so metadata is returned without reading the file back.
    ``owner_id`` is the resolved owner and every query filters by it: it is the
    security boundary. ``sha256`` is the chunked digest that lets a re-upload of
    the same bytes reuse the blob already in storage, blank on rows predating the
    column until ``agent_store_backfill_hashes`` fills them in.

    ``thread_id`` is a loose label a project may set; the reference store leaves
    it blank. Lifecycle runs on the ``ConversationAttachment`` relation, not
    on this column, which is kept only because projects read it.

    Used by ``DefaultAttachmentStore``.
    """

    attachment_id = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=255, blank=True, default="")
    thread_id = models.CharField(max_length=255, blank=True, default="")
    name = models.CharField(max_length=255, blank=True, default="")
    mime = models.CharField(max_length=255, blank=True, default="")
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    # Indexed because deleting a row has to ask whether any other row still
    # points at the same stored blob before removing the bytes. ``storage`` is a
    # callable so the migration records which backend to ask for rather than the
    # one a project happened to have configured when it was written.
    file = models.FileField(
        upload_to="django_pydantic_agent/attachments/",
        storage=attachment_storage,
        db_index=True,
    )
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
            # Owner first, and only ever queried with an owner. An index on
            # ``sha256`` alone would make the query this store must never run —
            # "who else holds these bytes?", across tenants — the cheap one.
            models.Index(fields=["owner_id", "sha256"]),
        ]

    def __str__(self) -> str:
        return self.name or self.attachment_id


class ConversationAttachment(models.Model):
    """One conversation's reference to one attachment.

    The through model behind ``StoredConversation.attachments``, a
    many-to-many rather than a foreign key on the attachment because both ends
    are plural: deduplication lets several rows describe the same bytes, and one
    attachment id may be quoted by more than one thread.

    Rows are derived, never hand-written — a conversation's save reconciles them
    from the attachment ids its own messages carry. Both sides cascade, so
    deleting either end drops the link; the attachment row outlives the link and
    is collected separately once nothing references it.
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
# The four models below back ``DefaultStepStore``. Each mirrors one
# ``pydantic-ai-harness`` dataclass (``RunRecord`` / ``StepEvent`` /
# ``ContinuableSnapshot`` / ``ToolEffectRecord``) plus an ``owner_id`` every query
# filters by — the security boundary, which the harness types do not carry.
# Optional lineage strings are ``null=True`` rather than blank so a ``None``
# sentinel round-trips exactly; ``list_runs`` filters on it.


class StoredRun(models.Model):
    """Lineage metadata for one agent run, one row per ``(owner_id, run_id)``.

    ``conversation_id`` groups a dialogue's runs and ``parent_run_id`` records
    which run spawned this one — two independent axes, so a delegated run may
    share a conversation while pointing at a different orchestrator run.
    ``started_at`` is the harness-supplied instant persisted verbatim, not
    stamped at insert, and orders ``list_runs``.
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
    """One append-only step event at a run/model/tool boundary.

    Never mutated — a correction is a follow-up row, and ``list_events`` returns
    them in insertion order (by ``id``). ``kind`` is a harness ``EventKind``
    literal; ``error`` carries ``repr(exc)`` on the ``*_failed`` kinds.
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
    """A message history a run can be resumed or forked from.

    ``messages`` is the full ``list[ModelMessage]`` serialised with
    ``ModelMessagesTypeAdapter``, ready for ``Agent.run(message_history=...)``.
    ``latest_snapshot`` picks the most recent by insertion order (largest
    ``id``), not by ``step_index``, matching the harness stores.

    ``state`` mirrors harness's ``SnapshotState``: ``complete`` sits at a
    boundary where every tool call has a matching return and is always safe to
    resume from, while ``interrupted`` is a mid-tool-cycle rescue point whose
    pending calls may need re-executing or closing out. It is stored rather than
    inferred because by the time a resume is attempted the run that produced the
    row is gone.
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
    """A tool call's side-effect status.

    Upserted on ``(owner_id, run_id, tool_call_id)`` as the call moves
    ``started`` to ``completed`` / ``failed``. Still ``started`` after a process
    restart means the external effect may or may not have landed;
    ``idempotency_key`` / ``effect_summary`` are what let an orchestrator decide
    whether replay is safe. ``list_unresolved_tool_effects`` returns those rows.
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
