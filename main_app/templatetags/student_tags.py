from django import template
from ..models import StudentAdmission, AcademicSession

register = template.Library()

@register.filter
def has_new_record(student, active_session):
    return StudentAdmission.objects.filter(
        first_name=student.first_name,
        last_name=student.last_name,
        academic_session=active_session
    ).exists()