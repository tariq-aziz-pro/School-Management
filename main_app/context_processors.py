from .models import AcademicSession

def active_session(request):
    if request.user.is_authenticated and request.user.user_type == 1 and request.user.school:
        session = AcademicSession.objects.filter(school=request.user.school, is_active=True).first()
    else:
        session = None
    return {'active_session': session}
