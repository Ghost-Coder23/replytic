from django import template

register = template.Library()


@register.filter
def split(value, separator="|"):
    """Split a string, e.g. ``"a|b|c"|split:"|"``. Lets templates pass short lists to includes."""
    return [part.strip() for part in str(value).split(separator) if part.strip()]
