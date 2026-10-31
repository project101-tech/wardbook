"""Small helpers for tests: put users into networks without going through the sign-up flow."""
from .models import Environment, EnvironmentMembership


def env(name="Ward 11-A", **kw):
    e, _ = Environment.objects.get_or_create(name=name, defaults=kw)
    return e


def join(user, *envs, role="member", status="approved"):
    """Make `user` an approved member of each network (the shared test network if none given)."""
    for e in envs or (env(),):
        EnvironmentMembership.objects.update_or_create(user=user, environment=e, defaults={"role": role, "status": status})
    return user
