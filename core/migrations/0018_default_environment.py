"""Everything that exists today becomes the first network, so nothing changes for current residents.

The network is named after the SOCIETY_NAME setting (rename it in the admin whenever you like).
Active residents are approved members; accounts still waiting for activation stay "waiting for approval"; staff become network admins.
"""
from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    Environment = apps.get_model("core", "Environment")
    Membership = apps.get_model("core", "EnvironmentMembership")
    User = apps.get_model("core", "User")
    Post = apps.get_model("core", "Post")
    Announcement = apps.get_model("core", "Announcement")
    PaymentRequest = apps.get_model("core", "PaymentRequest")
    Community = apps.get_model("core", "Community")

    name = (getattr(settings, "SOCIETY_NAME", "") or "Our Society").strip()[:60] or "Our Society"
    from django.utils.text import slugify
    env = Environment.objects.filter(name=name).first() or Environment.objects.create(
        name=name, slug=slugify(name)[:60] or "society", description="Your residential society")

    Membership.objects.bulk_create([
        Membership(user_id=u.pk, environment=env,
                   role="admin" if u.is_staff else "member",
                   status="approved" if u.is_active else "pending")
        for u in User.objects.all().only("pk", "is_active", "is_staff")
    ], ignore_conflicts=True)

    for model, field in ((Post, "environments"), (Announcement, "environments"), (PaymentRequest, "environments")):
        through = getattr(model, field).through
        fk = model._meta.model_name + "_id"
        through.objects.bulk_create(
            [through(**{fk: pk, "environment_id": env.pk}) for pk in model.objects.values_list("pk", flat=True)],
            ignore_conflicts=True, batch_size=500)
    Community.objects.filter(environment__isnull=True).update(environment=env)


class Migration(migrations.Migration):
    dependencies = [("core", "0017_environments")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
