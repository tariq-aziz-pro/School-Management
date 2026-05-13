"""
Role helpers and view decorators for school-scoped access control.

User types (CustomUser.user_type):
  1 = Admin, 2 = Operator, 3 = Owner  → school staff (operational portal)
  4 = Teacher, 5 = Student
Platform operators use Django is_superuser (developer tools).
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from .models import AcademicSession

USER_TYPE_ADMIN = 1
USER_TYPE_OPERATOR = 2
USER_TYPE_OWNER = 3
USER_TYPE_TEACHER = 4
USER_TYPE_STUDENT = 5

SCHOOL_STAFF_TYPES = frozenset(
    (USER_TYPE_ADMIN, USER_TYPE_OPERATOR, USER_TYPE_OWNER)
)


def is_school_staff(user):
    return user.is_authenticated and getattr(user, "user_type", None) in SCHOOL_STAFF_TYPES


def is_teacher(user):
    return user.is_authenticated and user.user_type == USER_TYPE_TEACHER


def is_student(user):
    return user.is_authenticated and user.user_type == USER_TYPE_STUDENT


def is_developer(user):
    """Platform-level access (Django superuser)."""
    return user.is_authenticated and user.is_superuser


def role_home_url(user):
    """Default home after login or when blocking an unauthorized area."""
    if not user.is_authenticated:
        return "login"
    if is_developer(user) and user.user_type != USER_TYPE_ADMIN:
        return "developer_dashboard"
    if is_school_staff(user):
        return "dashboard"
    if is_teacher(user):
        return "teacher_dashboard"
    if is_student(user):
        return "student_dashboard"
    return "index"


def get_active_session(request):
    """
    Active AcademicSession for the current user's school (via user.school).
    Cached on the request object.
    """
    if not hasattr(request, "_active_session"):
        school = getattr(request.user, "school", None)
        if request.user.is_authenticated and school:
            request._active_session = AcademicSession.objects.filter(
                school=school, is_active=True
            ).first()
        else:
            request._active_session = None
    return request._active_session


def school_staff_required(view_func):
    """Admin, Operator, or Owner with a linked school."""

    @wraps(view_func)
    @login_required(login_url="login")
    def _wrapped(request, *args, **kwargs):
        if not is_school_staff(request.user):
            messages.warning(
                request, "You do not have permission to access that page."
            )
            return redirect(role_home_url(request.user))
        if not getattr(request.user, "school_id", None):
            messages.error(
                request,
                "Your account is not linked to a school. Contact your administrator.",
            )
            return redirect("login")
        return view_func(request, *args, **kwargs)

    return _wrapped


def teacher_required(view_func):
    """Teacher accounts only (must belong to a school)."""

    @wraps(view_func)
    @login_required(login_url="login")
    def _wrapped(request, *args, **kwargs):
        if not is_teacher(request.user):
            messages.warning(request, "That area is restricted to teachers.")
            return redirect(role_home_url(request.user))
        if not getattr(request.user, "school_id", None):
            messages.error(
                request, "Your teacher account is not linked to a school."
            )
            return redirect("login")
        return view_func(request, *args, **kwargs)

    return _wrapped


def student_required(view_func):
    """Student accounts only."""

    @wraps(view_func)
    @login_required(login_url="login")
    def _wrapped(request, *args, **kwargs):
        if not is_student(request.user):
            messages.warning(request, "That area is restricted to students.")
            return redirect(role_home_url(request.user))
        return view_func(request, *args, **kwargs)

    return _wrapped


def school_staff_or_teacher_required(view_func):
    """School staff (admin/operator/owner) or teacher."""

    @wraps(view_func)
    @login_required(login_url="login")
    def _wrapped(request, *args, **kwargs):
        if not (is_school_staff(request.user) or is_teacher(request.user)):
            messages.warning(
                request, "You do not have permission to access that page."
            )
            return redirect(role_home_url(request.user))
        if not getattr(request.user, "school_id", None):
            messages.error(
                request, "Your account is not linked to a school."
            )
            return redirect("login")
        return view_func(request, *args, **kwargs)

    return _wrapped


def portal_user_required(view_func):
    """Anyone using the school portal: staff, teacher, or student."""

    @wraps(view_func)
    @login_required(login_url="login")
    def _wrapped(request, *args, **kwargs):
        if not (
            is_school_staff(request.user)
            or is_teacher(request.user)
            or is_student(request.user)
        ):
            messages.warning(
                request, "You do not have permission to access that page."
            )
            return redirect(role_home_url(request.user))
        if not getattr(request.user, "school_id", None):
            messages.error(
                request, "Your account is not linked to a school."
            )
            return redirect("login")
        return view_func(request, *args, **kwargs)

    return _wrapped


def developer_required(view_func):
    """Django superusers only (platform / billing tools)."""

    @wraps(view_func)
    @login_required(login_url="login")
    def _wrapped(request, *args, **kwargs):
        if not is_developer(request.user):
            messages.error(request, "That area is for platform administrators only.")
            return redirect(role_home_url(request.user))
        return view_func(request, *args, **kwargs)

    return _wrapped
