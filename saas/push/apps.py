from django.apps import AppConfig


class PushConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "push"

    def ready(self):
        from django.contrib.auth.signals import user_logged_out
        from .views import deactivate_on_logout
        user_logged_out.connect(deactivate_on_logout, dispatch_uid="mpno_unsubscribe_device_on_logout")
