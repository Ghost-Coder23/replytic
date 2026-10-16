from django import forms


class BootstrapFormMixin:
    """Adds Bootstrap 5 classes to every widget so forms match the design system."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            existing = widget.attrs.get("class", "")
            if isinstance(widget, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css = "form-select"
            elif isinstance(widget, forms.FileInput):
                css = "form-control"
            else:
                css = "form-control"
            widget.attrs["class"] = f"{existing} {css}".strip()

    def full_clean(self):
        super().full_clean()
        # Flag invalid inputs so the design system can highlight them.
        for name in self._errors or {}:
            field = self.fields.get(name)
            if field is not None and "is-invalid" not in field.widget.attrs.get("class", ""):
                field.widget.attrs["class"] = f"{field.widget.attrs.get('class', '')} is-invalid".strip()
