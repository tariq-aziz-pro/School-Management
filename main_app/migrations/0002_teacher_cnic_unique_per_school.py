from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('main_app', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='teacher',
            name='cnic',
            field=models.CharField(
                max_length=15,
                validators=[django.core.validators.RegexValidator(r'^\\d{5}-\\d{7}-\\d$', 'Enter valid CNIC format.')],
            ),
        ),
        migrations.AlterUniqueTogether(
            name='teacher',
            unique_together={('school', 'cnic')},
        ),
    ]
