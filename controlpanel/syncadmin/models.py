"""Data model for the sync control panel (SQLite via Django ORM)."""

from __future__ import annotations

from django.db import models

from .services import secrets


class AgentSettings(models.Model):
    """Singleton (pk=1) holding all runtime configuration."""

    # --- Access (.accdb) ---
    access_db_path = models.CharField(max_length=500, blank=True)
    access_db_password_enc = models.CharField(max_length=500, blank=True)

    # --- PostgreSQL (OmSree CRM) ---
    pg_host = models.CharField(max_length=255, blank=True, default="localhost")
    pg_port = models.IntegerField(default=5432)
    pg_dbname = models.CharField(max_length=255, blank=True)
    pg_user = models.CharField(max_length=255, blank=True)
    pg_password_enc = models.CharField(max_length=500, blank=True)
    pg_sslmode = models.CharField(max_length=20, default="prefer")

    # --- Direction toggles ---
    web_to_access_enabled = models.BooleanField(default=True)
    access_to_web_enabled = models.BooleanField(default=False)

    # --- Behaviour ---
    dry_run = models.BooleanField(default=True)
    sync_interval_minutes = models.PositiveIntegerField(default=15)
    cron_expr = models.CharField(max_length=120, blank=True, help_text="optional 5-field cron; overrides interval")
    scheduler_paused = models.BooleanField(default=False)
    state_dir = models.CharField(max_length=500, blank=True)

    outbound_cursor = models.CharField(max_length=120, blank=True)
    inbound_cursor = models.CharField(max_length=120, blank=True)

    # --- Email alerts ---
    alerts_enabled = models.BooleanField(default=False)
    smtp_host = models.CharField(max_length=255, blank=True)
    smtp_port = models.IntegerField(default=587)
    smtp_user = models.CharField(max_length=255, blank=True)
    smtp_password_enc = models.CharField(max_length=500, blank=True)
    smtp_use_tls = models.BooleanField(default=True)
    alert_from = models.CharField(max_length=255, blank=True)
    alert_recipients = models.CharField(max_length=1000, blank=True, help_text="comma-separated")

    class Meta:
        verbose_name = "Agent settings"
        verbose_name_plural = "Agent settings"

    def __str__(self):
        return "Agent settings"

    # Encrypted-password convenience accessors -----------------------------
    @property
    def access_db_password(self) -> str:
        return secrets.decrypt(self.access_db_password_enc)

    @access_db_password.setter
    def access_db_password(self, value: str):
        self.access_db_password_enc = secrets.encrypt(value or "")

    @property
    def pg_password(self) -> str:
        return secrets.decrypt(self.pg_password_enc)

    @pg_password.setter
    def pg_password(self, value: str):
        self.pg_password_enc = secrets.encrypt(value or "")

    @property
    def smtp_password(self) -> str:
        return secrets.decrypt(self.smtp_password_enc)

    @smtp_password.setter
    def smtp_password(self, value: str):
        self.smtp_password_enc = secrets.encrypt(value or "")

    @property
    def recipient_list(self) -> list[str]:
        return [r.strip() for r in self.alert_recipients.split(",") if r.strip()]

    @classmethod
    def get_solo(cls) -> "AgentSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class TableMapping(models.Model):
    WEB2ACCESS = "web2access"
    ACCESS2WEB = "access2web"
    DIRECTIONS = [(WEB2ACCESS, "web -> access"), (ACCESS2WEB, "access -> web")]
    CONFLICTS = [("web-wins", "web-wins"), ("newest-wins", "newest-wins")]

    direction = models.CharField(max_length=12, choices=DIRECTIONS, default=WEB2ACCESS)
    access_table = models.CharField(max_length=255)
    pg_table = models.CharField(max_length=255, blank=True, help_text="CRM PostgreSQL table")
    web_entity = models.CharField(max_length=255, blank=True)
    key_column = models.CharField(max_length=255, help_text="Access key column")
    conflict_rule = models.CharField(max_length=12, choices=CONFLICTS, default="web-wins")
    cursor_column = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["order", "access_table"]
        unique_together = [("direction", "access_table")]

    def __str__(self):
        return f"{self.access_table} ({self.get_direction_display()})"

    @property
    def active_fields(self):
        return self.fields.filter(is_active=True)


class FieldMapping(models.Model):
    ROLES = [
        ("key", "key"),
        ("sync", "sync"),
        ("no", "no (never leaves source)"),
        ("review", "review (pending sign-off)"),
        ("candidate", "candidate (inbound)"),
        ("match", "match (natural-key resolution)"),
    ]
    table = models.ForeignKey(TableMapping, related_name="fields", on_delete=models.CASCADE)
    access_column = models.CharField(max_length=255)
    crm_field = models.CharField(max_length=255, help_text="PostgreSQL column")
    data_type = models.CharField(max_length=50, blank=True)
    role = models.CharField(max_length=12, choices=ROLES, default="sync")
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=500, blank=True)
    # Optional JSON {source_value: target_value} applied to this field's values when
    # syncing (e.g. access->web status id -> web status text). Keys compared as str.
    value_map = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]
        unique_together = [("table", "access_column")]

    def __str__(self):
        return f"{self.table.access_table}.{self.access_column} <- {self.crm_field}"


class SyncRun(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    trigger = models.CharField(max_length=20, default="manual")  # manual | scheduled
    direction = models.CharField(max_length=12, default="web2access")
    status = models.CharField(max_length=12, default="ok")  # ok | dry-run | error
    tables_processed = models.IntegerField(default=0)
    rows_written = models.IntegerField(default=0)
    cursor = models.CharField(max_length=120, blank=True)
    error_message = models.TextField(blank=True)
    traceback = models.TextField(blank=True)
    detail = models.TextField(blank=True)
    duration_ms = models.IntegerField(default=0)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.started_at:%Y-%m-%d %H:%M} {self.direction} {self.status}"
