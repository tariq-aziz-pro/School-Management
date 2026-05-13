from django import template

register = template.Library()


@register.filter
def message_bootstrap_class(tags):
    """Map Django message tags to Bootstrap 5 alert-* classes (e.g. error → danger)."""
    if not tags:
        return "info"
    t = str(tags).lower()
    if "error" in t:
        return "danger"
    if "success" in t:
        return "success"
    if "warning" in t:
        return "warning"
    if "debug" in t:
        return "secondary"
    return "info"
