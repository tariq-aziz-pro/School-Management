from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import School, CustomUser, StudentAdmission, MonthlyFee


@receiver(post_save, sender=School)
def update_users_on_school_status_change(sender, instance, **kwargs):
    """
    Deactivate/activate all users of a school when school status changes.
    """
    users = CustomUser.objects.filter(school=instance)
    updated_count = users.update(is_active=instance.is_active)

    # Debug log
    print(f"[Signal] Updated {updated_count} users for school '{instance.school_name}' to is_active={instance.is_active}")


@receiver(post_save, sender=StudentAdmission)
def create_monthly_fee_for_new_admission(sender, instance, created, **kwargs):
    """
    Automatically create a MonthlyFee record when a student is admitted.
    """
    if created and instance.student:
        try:
            school = instance.student.school  # ✅ school comes from Student

            MonthlyFee.objects.create(
                student=instance.student,
                admission=instance,   # if MonthlyFee is linked to admission too
                month=timezone.now().month,
                year=timezone.now().year,
                total_dues=instance.total_dues,
                received=instance.received,
                discount=instance.discount,
                balance=instance.balance,
                school=school
            )
            print(f"[Signal] MonthlyFee created for {instance.student} in {school}")

        except Exception as e:
            print(f"[Signal Error] Failed to create MonthlyFee for {instance.student}: {e}")


