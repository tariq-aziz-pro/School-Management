from .models import AcademicSession
from .permissions import (
    is_developer,
    is_school_staff,
    is_student,
    is_teacher,
)


def active_session(request):
    """Expose active academic session for any user linked to a school."""
    if request.user.is_authenticated and getattr(request.user, "school_id", None):
        session = AcademicSession.objects.filter(
            school=request.user.school, is_active=True
        ).first()
    else:
        session = None
    return {"active_session": session}


def user_roles(request):
    """Template-friendly role flags (prefer these over raw user_type in templates)."""
    u = request.user
    if not u.is_authenticated:
        return {
            "is_school_staff": False,
            "is_teacher": False,
            "is_student": False,
            "is_platform_developer": False,
        }
    return {
        "is_school_staff": is_school_staff(u),
        "is_teacher": is_teacher(u),
        "is_student": is_student(u),
        "is_platform_developer": is_developer(u),
    }
