"""
Warehouse name becomes a bilingual _fr/_en pair.

RenameField, not add-and-drop: the autodetector would treat `name` ->
`name_fr` as an unrelated new column and lose every existing warehouse name.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="warehouse",
            old_name="name",
            new_name="name_fr",
        ),
        migrations.AlterField(
            model_name="warehouse",
            name="name_fr",
            field=models.CharField(max_length=120, verbose_name="name (French)"),
        ),
        migrations.AddField(
            model_name="warehouse",
            name="name_en",
            field=models.CharField(
                blank=True, max_length=120, verbose_name="name (English)"
            ),
        ),
    ]
