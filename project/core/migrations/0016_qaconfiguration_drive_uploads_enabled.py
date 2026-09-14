from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_historylog_modality_changed_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="qaconfiguration",
            name="drive_uploads_enabled",
            field=models.BooleanField(default=False, verbose_name="Subir recibos a Google Drive (pruebas)"),
        ),
    ]
