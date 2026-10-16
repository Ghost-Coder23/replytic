"""Account business logic (kept out of views)."""
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from organizations.services import create_organization

User = get_user_model()


class EmailAlreadyRegistered(Exception):
    pass


def email_in_use(email, *, exclude_user=None):
    queryset = User.objects.filter(email__iexact=email) | User.objects.filter(username__iexact=email)
    if exclude_user is not None:
        queryset = queryset.exclude(pk=exclude_user.pk)
    return queryset.exists()


@transaction.atomic
def register_user_with_organization(*, first_name, last_name, email, password, business_name):
    """
    Create the user, their organization and an OWNER membership atomically.
    Returns ``(user, organization, membership)``.
    """
    email = email.strip().lower()
    if email_in_use(email):
        raise EmailAlreadyRegistered(email)
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                username=email, email=email, password=password,
                first_name=first_name.strip(), last_name=last_name.strip(),
            )
    except IntegrityError as exc:
        raise EmailAlreadyRegistered(email) from exc
    organization, membership = create_organization(owner=user, name=business_name)
    return user, organization, membership


def update_account(*, user, first_name, last_name, email, previous_email=None):
    """
    Update the signed-in user's own details, keeping username in sync with email.

    ``previous_email`` is needed when a ModelForm has already written the new
    email onto ``user`` before this is called.
    """
    email = email.strip().lower()
    previous_email = (previous_email if previous_email is not None else user.email or "").lower()
    user.first_name = first_name.strip()
    user.last_name = last_name.strip()
    user.email = email
    if user.username.lower() == previous_email:
        user.username = email
    user.save()
    return user
