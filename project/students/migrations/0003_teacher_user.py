from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0002_alter_studentparent_unique_together_student_gender_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="teacher",
            name="user",
            field=models.OneToOneField(
                blank=True,
                help_text="Django auth user — login identity + hashed password for this teacher.",
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="teacher",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
