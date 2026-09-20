from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0018_student_pickup_authorized"),
    ]

    operations = [
        migrations.AddField(
            model_name="teacher",
            name="tester",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Cuenta de PRUEBAS pública (solo en testing). No es administradora: "
                    "ve bastante más que una profesora normal, pero solo lo que "
                    "TESTER_ALLOWED_URL_NAMES permite, y nunca /admin/ ni el panel de QA."
                ),
            ),
        ),
    ]
