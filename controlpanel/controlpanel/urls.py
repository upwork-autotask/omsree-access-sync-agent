from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from syncadmin import views

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="syncadmin/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("", views.dashboard, name="dashboard"),
    path("sync-now/", views.sync_now, name="sync_now"),
    path("toggle/<str:direction>/", views.toggle_direction, name="toggle_direction"),

    path("connections/", views.connections, name="connections"),
    path("connections/test/<str:which>/", views.test_connection, name="test_connection"),

    path("settings/", views.settings_view, name="settings"),
    path("email/", views.email_settings, name="email_settings"),
    path("email/test/", views.test_email, name="test_email"),

    path("schedule/", views.schedule_view, name="schedule"),

    path("mappings/", views.mappings, name="mappings"),
    path("mappings/add/", views.add_table_mapping, name="add_table_mapping"),
    path("mappings/<int:pk>/", views.edit_table_mapping, name="edit_table_mapping"),
    path("mappings/<int:pk>/delete/", views.delete_table_mapping, name="delete_table_mapping"),
    path("mappings/field/<int:pk>/delete/", views.delete_field_mapping, name="delete_field_mapping"),
    path("mappings/import-csv/", views.import_csv, name="import_csv"),
    path("mappings/export-csv/", views.export_csv, name="export_csv"),
    path("mappings/reseed/", views.reseed_from_docs, name="reseed_from_docs"),

    # JSON endpoints for the live dropdowns
    path("api/tables/<str:source>/", views.api_tables, name="api_tables"),
    path("api/columns/<str:source>/", views.api_columns, name="api_columns"),

    path("logs/", views.logs, name="logs"),
]
