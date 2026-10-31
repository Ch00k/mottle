from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0011_access_token_not_null"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="playlistupdate",
            name="albums_removed",
        ),
        migrations.RemoveField(
            model_name="playlistupdate",
            name="tracks_removed",
        ),
    ]
