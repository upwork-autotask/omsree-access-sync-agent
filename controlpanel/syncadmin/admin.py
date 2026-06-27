from django.contrib import admin

from .models import AgentSettings, FieldMapping, SyncRun, TableMapping


class FieldMappingInline(admin.TabularInline):
    model = FieldMapping
    extra = 1
    fields = ("access_column", "crm_field", "data_type", "role", "is_active", "notes")


@admin.register(TableMapping)
class TableMappingAdmin(admin.ModelAdmin):
    list_display = ("access_table", "pg_table", "direction", "key_column", "conflict_rule", "is_active")
    list_filter = ("direction", "is_active", "conflict_rule")
    search_fields = ("access_table", "pg_table", "web_entity")
    inlines = [FieldMappingInline]


@admin.register(FieldMapping)
class FieldMappingAdmin(admin.ModelAdmin):
    list_display = ("table", "access_column", "crm_field", "role", "is_active")
    list_filter = ("role", "is_active", "table__direction")
    search_fields = ("access_column", "crm_field")


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "direction", "trigger", "status", "rows_written", "tables_processed")
    list_filter = ("status", "direction", "trigger")
    readonly_fields = [f.name for f in SyncRun._meta.fields]


@admin.register(AgentSettings)
class AgentSettingsAdmin(admin.ModelAdmin):
    # Secrets are write-only through the web UI; hide the encrypted blobs here.
    exclude = ("access_db_password_enc", "pg_password_enc", "smtp_password_enc")

    def has_add_permission(self, request):
        return not AgentSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
