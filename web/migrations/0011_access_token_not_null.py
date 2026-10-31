from django.db import migrations

import web.models


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0010_user_settings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="spotifyauth",
            name="access_token",
            field=web.models.EncryptedCharField(max_length=450),
        ),
    ]
