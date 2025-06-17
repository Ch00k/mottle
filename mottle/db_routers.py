# ruff: noqa: ARG002,ANN001,ANN003,ANN201
# type: ignore  # noqa: PGH003


class DefaultRouter:
    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label != "django_q" and db == "default":
            return True
        return None


class TasksRouter:
    def db_for_read(self, model, **hints):
        """
        Attempts to read auth and contenttypes models go to auth_db.
        """
        if model._meta.app_label == "django_q":
            return "tasks"
        return None

    def db_for_write(self, model, **hints):
        """
        Attempts to write auth and contenttypes models go to auth_db.
        """
        if model._meta.app_label == "django_q":
            return "tasks"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        """
        Allow relations if a model in the auth or contenttypes apps is
        involved.
        """
        if obj1._meta.app_label == "django_q" or obj2._meta.app_label == "django_q":
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return app_label == "django_q" and db == "tasks"
