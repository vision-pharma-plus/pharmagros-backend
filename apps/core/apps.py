from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = _("Core")

    def ready(self):
        # Registers the OpenAPI extensions by import side effect. Without this
        # the schema generator never sees them and falls back to warning about
        # the unrecognised authentication class.
        from . import schema  # noqa: F401
