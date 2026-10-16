from django.contrib.auth import get_user_model

from organizations.models import Organization, OrganizationMembership
from organizations.services import create_organization

User = get_user_model()
PASSWORD = "S3cure-Pass-Word!"
Role = OrganizationMembership.Role


def make_user(email, first_name="Test", last_name="User", password=PASSWORD):
    return User.objects.create_user(
        username=email, email=email, password=password, first_name=first_name, last_name=last_name
    )


def make_org_with_owner(name, owner_email):
    owner = make_user(owner_email, first_name=name.split()[0])
    organization, membership = create_organization(owner=owner, name=name)
    return organization, owner, membership


def add_member(organization, email, role=Role.AGENT, first_name="Member"):
    user = make_user(email, first_name=first_name)
    membership = OrganizationMembership.objects.create(user=user, organization=organization, role=role)
    return user, membership
