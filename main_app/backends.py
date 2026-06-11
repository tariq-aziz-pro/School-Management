from django.contrib import messages
from django.contrib.auth.backends import ModelBackend


class CustomAuthBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        user = super().authenticate(request, username, password, **kwargs)
        if (
            user
            and getattr(user, "user_type", None) in (1, 2, 3)
            and user.school
            and not user.school.is_active
        ):
            if request:
                messages.error(
                    request,
                    "Your account is blocked by the Powerlink Team due to payment issues.",
                )
            return None
        return user
