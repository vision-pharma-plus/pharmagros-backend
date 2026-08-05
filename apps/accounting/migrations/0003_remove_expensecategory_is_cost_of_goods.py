"""
Drop the cost-of-goods flag from expense categories.

The flag existed so the financial overview could hold shipping and clearing
out of `operating_expenses`, on the grounds that those charges already reach
margin through landed-cost apportionment on goods receipt. It is removed here,
so every expense category now counts toward operating expenses.

The column carried no data other than that classification, so the drop is
plain. Reversing the migration restores the column at its `False` default;
any category that had been flagged must be re-flagged by hand.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0002_expensecategory_localisation"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="expensecategory",
            name="is_cost_of_goods",
        ),
    ]
