import django.db.models.deletion
import scanner.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ScanHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('original_filename', models.CharField(blank=True, default='upload', max_length=255)),
                ('original_image', models.ImageField(upload_to=scanner.models.original_upload_to)),
                ('original_width', models.PositiveIntegerField(blank=True, null=True)),
                ('original_height', models.PositiveIntegerField(blank=True, null=True)),
                ('processed_image', models.ImageField(blank=True, upload_to=scanner.models.processed_upload_to)),
                ('processed_width', models.PositiveIntegerField(blank=True, null=True)),
                ('processed_height', models.PositiveIntegerField(blank=True, null=True)),
                ('thumb_image', models.ImageField(blank=True, upload_to=scanner.models.thumb_upload_to)),
                ('filter_used', models.CharField(blank=True, default='', max_length=20)),
                ('export_format', models.CharField(blank=True, default='', max_length=10)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('completed', 'Completed'), ('failed', 'Failed')], db_index=True, default='pending', max_length=16)),
                ('corners_json', models.JSONField(blank=True, null=True)),
                ('params_json', models.JSONField(blank=True, null=True)),
                ('page_order', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(blank=True, help_text='Set when a signed-in user owns the scan.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='scans', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Scan history entry',
                'verbose_name_plural': 'Scan history',
                'ordering': ['page_order', '-created_at'],
                'indexes': [models.Index(fields=['session_key', 'page_order'], name='scanner_sca_session_c34348_idx'), models.Index(fields=['user', '-created_at'], name='scanner_sca_user_id_8c123c_idx')],
            },
        ),
    ]
