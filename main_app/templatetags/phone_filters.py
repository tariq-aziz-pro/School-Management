from django import template

register = template.Library()

@register.filter
def clean_phone_number(phone):
    if not phone:
        return ''
    phone = phone.strip()
    # Remove any spaces, dashes, or other characters
    phone = phone.replace(' ', '').replace('-', '')
    # Assume Pakistani numbers: convert 03001234567 to +923001234567
    if phone.startswith('0'):
        phone = '+92' + phone[1:]
    elif not phone.startswith('+'):
        phone = '+92' + phone
    return phone