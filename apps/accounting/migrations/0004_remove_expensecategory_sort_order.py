"""
Drop the manual display-order column from expense categories.

`sort_order` existed only to control the position of a category in pickers and
listings — it never affected any figure. With it gone, categories order
alphabetically by name, so the seeded ordering that kept rent and salaries at
the top and "Other expenses" at the bottom no longer applies.

Reversing the migration restores the column at its `100` default, which is a
flat ordering: the original per-category numbers are not recoverable and would
have to be re-entered.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0003_remove_expensecategory_is_cost_of_goods"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="expensecategory",
            options={
                "ordering": ("name_fr",),
                "verbose_name": "expense category",
                "verbose_name_plural": "expense categories",
            },
        ),
        migrations.RemoveField(
            model_name="expensecategory",
            name="sort_order",
        ),
    ]
