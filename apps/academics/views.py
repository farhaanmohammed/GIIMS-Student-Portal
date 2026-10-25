from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, UpdateView

from apps.academics.forms import AcademicYearForm, BatchForm, CourseForm
from apps.academics.models import AcademicYear, Batch, Course
from apps.core.permissions import role_required

PAGE_SIZE = 25


def _parse_int(value: str | None) -> int | None:
    """Return an integer for a well-formed value, otherwise ``None``."""
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_is_active(value: str | None) -> str:
    """Allow only the documented ``"1"``/``"0"`` status filters."""
    return value if value in {"1", "0"} else ""


def _can_manage_academics(user: Any) -> bool:
    """Presentation helper only; write views enforce the role server-side."""
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, "role", None) in {"ADMIN", "MANAGEMENT"})
    )


class AcademicsListView(LoginRequiredMixin, ListView):
    """Shared paginated list behaviour with HTMX partial rendering."""

    paginate_by = PAGE_SIZE
    partial_template_name = ""

    def get_filters(self) -> dict[str, str]:
        """Return the normalized filters that were actually applied."""
        return {}

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        filters = self.get_filters()
        context["filters"] = filters
        context["querystring"] = urlencode_filters(filters)
        context["can_manage"] = _can_manage_academics(self.request.user)

        page_obj = context.get("page_obj")
        if page_obj is not None:
            context["total_count"] = page_obj.paginator.count
            context["page_range"] = page_obj.paginator.get_elided_page_range(page_obj.number)
        else:
            context["total_count"] = 0
            context["page_range"] = []
        return context

    def get_template_names(self) -> list[str]:
        if self.partial_template_name and self.request.headers.get("HX-Request"):
            return [self.partial_template_name]
        return [self.template_name]

    def paginate_queryset(self, queryset: QuerySet, page_size: int) -> tuple[Any, Any, Any, bool]:
        """Treat malformed or out-of-range page numbers as the nearest valid page."""
        paginator = self.get_paginator(queryset, page_size)
        page_number = self.request.GET.get(self.page_kwarg) or 1
        page = paginator.get_page(page_number)
        return paginator, page, page.object_list, page.has_other_pages()


def urlencode_filters(filters: dict[str, str]) -> str:
    """Build an escaped query string from validated filter values."""
    return urlencode({key: value for key, value in filters.items() if value})


class AcademicsWriteMixin:
    """
    Role gate for create/update/toggle views.

    Placed after ``LoginRequiredMixin`` in the MRO so anonymous users are
    redirected to the login page and only authenticated, unauthorized users
    receive a 403 response.
    """

    @method_decorator(role_required("ADMIN", "MANAGEMENT"))
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(request, *args, **kwargs)


class PostOnlyMixin:
    """Return 405 for anything other than POST."""

    @method_decorator(require_POST)
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(request, *args, **kwargs)


class AcademicYearListView(AcademicsListView):
    model = AcademicYear
    template_name = "academics/academic_year_list.html"
    partial_template_name = "academics/partials/_academic_year_table.html"
    context_object_name = "academic_years"

    def get_filters(self) -> dict[str, str]:
        cached = getattr(self, "_filters", None)
        if cached is not None:
            return cached
        self._filters = {"is_active": _parse_is_active(self.request.GET.get("is_active"))}
        return self._filters

    def get_queryset(self) -> QuerySet[AcademicYear]:
        queryset = AcademicYear.objects.all()
        is_active = self.get_filters()["is_active"]
        if is_active:
            queryset = queryset.filter(is_active=is_active == "1")
        return queryset


class AcademicYearCreateView(LoginRequiredMixin, AcademicsWriteMixin, CreateView):
    model = AcademicYear
    form_class = AcademicYearForm
    template_name = "academics/academic_year_form.html"
    success_url = reverse_lazy("academics:academic_year_list")

    def form_valid(self, form: AcademicYearForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Academic year "{self.object.label}" created.')
        return response


class AcademicYearUpdateView(LoginRequiredMixin, AcademicsWriteMixin, UpdateView):
    model = AcademicYear
    form_class = AcademicYearForm
    template_name = "academics/academic_year_form.html"
    success_url = reverse_lazy("academics:academic_year_list")

    def form_valid(self, form: AcademicYearForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Academic year "{self.object.label}" updated.')
        return response


class AcademicYearToggleView(LoginRequiredMixin, AcademicsWriteMixin, PostOnlyMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        academic_year = get_object_or_404(AcademicYear, pk=pk)
        academic_year.is_active = not academic_year.is_active
        academic_year.save(update_fields=["is_active", "updated_at"])
        state = "activated" if academic_year.is_active else "deactivated"
        messages.success(request, f'Academic year "{academic_year.label}" {state}.')
        return redirect("academics:academic_year_list")


class CourseListView(AcademicsListView):
    model = Course
    template_name = "academics/course_list.html"
    partial_template_name = "academics/partials/_course_table.html"
    context_object_name = "courses"

    def get_filters(self) -> dict[str, str]:
        cached = getattr(self, "_filters", None)
        if cached is not None:
            return cached
        self._filters = {
            "q": (self.request.GET.get("q") or "").strip()[:100],
            "is_active": _parse_is_active(self.request.GET.get("is_active")),
        }
        return self._filters

    def get_queryset(self) -> QuerySet[Course]:
        queryset = Course.objects.all()
        filters = self.get_filters()
        if filters["q"]:
            queryset = queryset.filter(
                Q(code__icontains=filters["q"]) | Q(name__icontains=filters["q"])
            )
        if filters["is_active"]:
            queryset = queryset.filter(is_active=filters["is_active"] == "1")
        return queryset


class CourseCreateView(LoginRequiredMixin, AcademicsWriteMixin, CreateView):
    model = Course
    form_class = CourseForm
    template_name = "academics/course_form.html"
    success_url = reverse_lazy("academics:course_list")

    def form_valid(self, form: CourseForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Course "{self.object.code}" created.')
        return response


class CourseUpdateView(LoginRequiredMixin, AcademicsWriteMixin, UpdateView):
    model = Course
    form_class = CourseForm
    template_name = "academics/course_form.html"
    success_url = reverse_lazy("academics:course_list")

    def form_valid(self, form: CourseForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Course "{self.object.code}" updated.')
        return response


class CourseToggleView(LoginRequiredMixin, AcademicsWriteMixin, PostOnlyMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        course = get_object_or_404(Course, pk=pk)
        course.is_active = not course.is_active
        course.save(update_fields=["is_active", "updated_at"])
        state = "activated" if course.is_active else "deactivated"
        messages.success(request, f'Course "{course.code}" {state}.')
        return redirect("academics:course_list")


class BatchListView(AcademicsListView):
    template_name = "academics/batch_list.html"
    partial_template_name = "academics/partials/_batch_table.html"
    context_object_name = "batches"

    def get_filters(self) -> dict[str, str]:
        cached = getattr(self, "_filters", None)
        if cached is not None:
            return cached
        centre = _parse_int(self.request.GET.get("centre"))
        academic_year = _parse_int(self.request.GET.get("academic_year"))

        filters = {
            "centre": "",
            "academic_year": "",
            "is_active": _parse_is_active(self.request.GET.get("is_active")),
        }

        if (
            centre is not None
            and self.request.user.get_accessible_centres().filter(pk=centre).exists()
        ):
            filters["centre"] = str(centre)
        if academic_year is not None and AcademicYear.objects.filter(pk=academic_year).exists():
            filters["academic_year"] = str(academic_year)

        self._filters = filters
        return self._filters

    def get_queryset(self) -> QuerySet[Batch]:
        queryset = Batch.objects.for_user(self.request.user).select_related(
            "centre", "course", "academic_year"
        )
        filters = self.get_filters()
        if filters["centre"]:
            queryset = queryset.filter(centre_id=filters["centre"])
        if filters["academic_year"]:
            queryset = queryset.filter(academic_year_id=filters["academic_year"])
        if filters["is_active"]:
            queryset = queryset.filter(is_active=filters["is_active"] == "1")
        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["centre_options"] = self.request.user.get_accessible_centres()
        context["academic_year_options"] = AcademicYear.objects.all()
        return context


class BatchCreateView(LoginRequiredMixin, AcademicsWriteMixin, CreateView):
    model = Batch
    form_class = BatchForm
    template_name = "academics/batch_form.html"
    success_url = reverse_lazy("academics:batch_list")

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form: BatchForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(
            self.request,
            f'Batch "{self.object.code}" created for {self.object.course.code}.',
        )
        return response


class BatchUpdateView(LoginRequiredMixin, AcademicsWriteMixin, UpdateView):
    form_class = BatchForm
    template_name = "academics/batch_form.html"
    success_url = reverse_lazy("academics:batch_list")

    def get_queryset(self) -> QuerySet[Batch]:
        # An inaccessible or other-centre pk resolves to 404, never 403.
        return Batch.objects.for_user(self.request.user).select_related(
            "centre", "course", "academic_year"
        )

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form: BatchForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(
            self.request,
            f'Batch "{self.object.code}" updated for {self.object.course.code}.',
        )
        return response


class BatchToggleView(LoginRequiredMixin, AcademicsWriteMixin, PostOnlyMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        queryset = Batch.objects.for_user(request.user).select_related("course")
        batch = get_object_or_404(queryset, pk=pk)
        batch.is_active = not batch.is_active
        batch.save(update_fields=["is_active", "updated_at"])
        state = "activated" if batch.is_active else "deactivated"
        messages.success(request, f'Batch "{batch.code}" {state}.')
        return redirect("academics:batch_list")
