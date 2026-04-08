import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main_app', '0015_events'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='syllabus',
            name='main_app_sy_subject_2adbbb_idx',
        ),
        migrations.AddField(
            model_name='syllabus',
            name='school',
            field=models.ForeignKey(default=2, on_delete=django.db.models.deletion.CASCADE, to='main_app.school'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='syllabus',
            name='uploaded_by',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterUniqueTogether(
            name='syllabus',
            unique_together={('school', 'subject', 'class_name', 'academic_session')},
        ),
        migrations.AlterField(
            model_name='syllabus',
            name='academic_session',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='main_app.academicsession'),
        ),
        migrations.AlterField(
            model_name='syllabus',
            name='class_name',
            field=models.CharField(max_length=50),
        ),
        migrations.AlterField(
            model_name='syllabus',
            name='description',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='syllabus',
            name='file',
            field=models.FileField(blank=True, null=True, upload_to='syllabus/'),
        ),
        migrations.AlterField(
            model_name='syllabus',
            name='subject',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='main_app.subject'),
        ),
        migrations.RemoveField(
            model_name='syllabus',
            name='created_by',
        ),
    ]
