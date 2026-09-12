"""Add the academy's fiscal details to SiteConfiguration (v1.27).

`pdf_service._get_academy_info()` has always read `academy_name`,
`academy_address`, `academy_cif`, `academy_phone` and `academy_website` off the
config with `getattr(config, ..., "")` — and none of them existed, so every value
fell through to the hard-coded `AcademyInfo` defaults and `academy_cif` fell
through to the empty string. The tax certificate asserts IRPF validity while
naming no CIF, which is the one field that makes it deductible: the document was
cosmetically fine and fiscally useless, with nowhere in the app to correct it.

Defaults mirror `AcademyInfo` so a fresh install keeps producing the document it
produces today. All five are `blank=True` because `update_site_config` runs
`full_clean()` on every price edit and a non-blank CharField would 400 all of
them until somebody filled these in.

Also re-states the Expense recurrence help_text in Spanish (it is rendered on the
admin form) and gives the three recurrence fields Spanish verbose names. Metadata
only — no schema change from those three, but Django emits the AlterFields and
they must be committed or `makemigrations --check` fails CI.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0012_alter_enrollmenttype_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfiguration",
            name="academy_name",
            field=models.CharField(
                blank=True,
                default="Five a Day English Academy",
                max_length=120,
                verbose_name="Nombre fiscal de la academia",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="academy_cif",
            field=models.CharField(
                blank=True,
                default="",
                help_text=("Aparece en los certificados fiscales; sin él el certificado no sirve para la declaración."),
                max_length=20,
                verbose_name="CIF/NIF",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="academy_address",
            field=models.CharField(
                blank=True,
                default="C/ Hermanos Jiménez 25 · 02004 Albacete",
                max_length=200,
                verbose_name="Dirección",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="academy_phone",
            field=models.CharField(
                blank=True,
                default="967 049 096",
                max_length=30,
                verbose_name="Teléfono",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="academy_website",
            field=models.CharField(
                blank=True,
                default="www.fiveadayenglish.com",
                max_length=120,
                verbose_name="Web",
            ),
        ),
        migrations.AlterField(
            model_name="expense",
            name="recurring_day",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text=(
                    "Día del mes (1–31). Lo usan las plantillas mensuales y anuales. "
                    "Los días que no existen en un mes corto se ajustan a su último día, "
                    "así que 29, 30 o 31 significan «el último día del mes»."
                ),
                null=True,
                verbose_name="Día del mes",
            ),
        ),
        migrations.AlterField(
            model_name="expense",
            name="recurring_month",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Mes (1–12) en el que se genera una plantilla anual.",
                null=True,
                verbose_name="Mes",
            ),
        ),
        migrations.AlterField(
            model_name="expense",
            name="recurring_weekdays",
            field=models.CharField(
                blank=True,
                default="",
                help_text=("Números de día separados por comas (0=lunes … 6=domingo) para plantillas semanales."),
                max_length=20,
                verbose_name="Días de la semana",
            ),
        ),
    ]
