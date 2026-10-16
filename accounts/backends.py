from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameBackend(ModelBackend):
    """Authenticate with an email address (case-insensitive) or a username."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        identifier = (username or kwargs.get(User.USERNAME_FIELD) or "").strip()
        if not identifier or password is None:
            return None
        candidates = User.objects.filter(
            Q(email__iexact=identifier) | Q(username__iexact=identifier)
        ).order_by("id")
        for user in candidates:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        # Run the hasher once to keep timing similar when the user is unknown.
        User().set_password(password)
        return None
