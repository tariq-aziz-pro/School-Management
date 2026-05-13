from django.contrib.auth import logout
from django.shortcuts import redirect
from django.contrib import messages
import logging

from .permissions import SCHOOL_STAFF_TYPES

logger = logging.getLogger(__name__)

class SchoolAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            logger.debug(f"Middleware: User={request.user.username}, Superuser={request.user.is_superuser}, UserType={request.user.user_type}, Path={request.path}")
            if request.user.is_superuser and request.user.user_type != 1 and request.path.startswith('/dashboard'):
                logger.debug("Redirecting superuser to developer_dashboard")
                return redirect('developer_dashboard')
            if (
                getattr(request.user, "user_type", None) in SCHOOL_STAFF_TYPES
                and request.user.school
                and not request.user.school.is_active
            ):
                logger.debug(
                    f"Logging out school staff {request.user.username} due to inactive school"
                )
                logout(request)
                messages.error(request, "Your account is blocked by the Powerlink Team due to payment issues.")
                return redirect('login')
        return self.get_response(request)