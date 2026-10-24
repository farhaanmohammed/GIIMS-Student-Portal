from functools import wraps
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import HttpRequest

from apps.core.models import TimeStampedModel


class CentreScopedQuerySet(models.QuerySet):
    """
    A queryset that enforces centre-level data scoping on the server side.
    """

    def for_user(self, user: Any) -> models.QuerySet:
        if not user or not user.is_authenticated:
            return self.none()

        # Users with all-centres access (Admins, Management, Superusers) see everything
        if getattr(user, "has_all_centres_access", False) or user.is_superuser:
            return self.all()

        accessible_centres = user.get_accessible_centres()
        return self.filter(centre__in=accessible_centres)


class CentreScopedManager(models.Manager.from_queryset(CentreScopedQuerySet)):
    """
    Default manager for models that have a direct ForeignKey to `centres.Centre`.
    """


class CentreScopedModel(TimeStampedModel):
    """
    Abstract base model for domain entities scoped to a single Centre.
    """

    centre = models.ForeignKey(
        "centres.Centre",
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_set",
        db_index=True,
    )

    objects = CentreScopedManager()

    class Meta:
        abstract = True


class CentreAccessRequiredMixin:
    """
    CBV mixin that enforces centre-level authorization on the requested object.
    """

    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
        user = self.request.user
        if not hasattr(obj, "centre"):
            return obj

        if not user.is_authenticated:
            raise PermissionDenied("Authentication required.")

        if not user.can_access_centre(obj.centre):
            raise PermissionDenied(f"You do not have access to centre {obj.centre}.")

        return obj


def role_required(*allowed_roles: str):
    """
    Decorator to restrict view access to specific user roles or superusers.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request: HttpRequest, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                raise PermissionDenied("Authentication required.")

            if user.is_superuser:
                return view_func(request, *args, **kwargs)

            user_role = getattr(user, "role", None)
            if user_role in allowed_roles:
                return view_func(request, *args, **kwargs)

            raise PermissionDenied("You do not have the required role to access this resource.")

        return _wrapped_view

    return decorator
