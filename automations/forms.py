from django import forms
from django.conf import settings

from core.forms import BootstrapFormMixin
from facebook.models import FacebookPage

from . import engine
from .models import AutomationRule

MAX_KEYWORDS = 30
MAX_KEYWORD_LENGTH = 80


def _clean_lines(value):
    seen, lines = set(), []
    for line in (value or "").splitlines():
        line = " ".join(line.split())
        if line and line.casefold() not in seen:
            seen.add(line.casefold())
            lines.append(line)
    return lines


class AutomationRuleForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = AutomationRule
        fields = ["name", "page", "keywords", "match_mode", "exclude_keywords", "action", "reply_template", "priority", "is_active"]
        labels = {
            "name": "Rule name", "page": "Applies to", "keywords": "When a comment contains",
            "match_mode": "Match", "exclude_keywords": "Except when it contains", "action": "Then",
            "reply_template": "Reply", "is_active": "Rule is active",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Price questions"}),
            "keywords": forms.Textarea(attrs={"rows": 4, "placeholder": "price\\nhow much\\ncost"}),
            "exclude_keywords": forms.Textarea(attrs={"rows": 2}),
            "reply_template": forms.Textarea(attrs={"rows": 4, "placeholder": "Hi {first_name}, thanks for asking! Message us on WhatsApp {whatsapp} and we'll help right away."}),
            "priority": forms.NumberInput(attrs={"min": 1, "max": 9999}),
        }

    def __init__(self, *args, organization, **kwargs):
        self.organization = organization
        super().__init__(*args, **kwargs)
        # Tenant isolation: only this organization's Pages can be chosen.
        self.fields["page"].queryset = FacebookPage.objects.filter(organization=organization).exclude(status=FacebookPage.Status.DISCONNECTED)
        self.fields["page"].empty_label = "All my Pages"
        self.fields["page"].required = False
        self.fields["reply_template"].required = False
        self.fields["keywords"].error_messages["required"] = "Add at least one keyword or phrase."

    def clean_keywords(self):
        lines = _clean_lines(self.cleaned_data.get("keywords"))
        if not lines:
            raise forms.ValidationError("Add at least one keyword or phrase.")
        if len(lines) > MAX_KEYWORDS:
            raise forms.ValidationError(f"Use {MAX_KEYWORDS} keywords or fewer. Split them across rules instead.")
        if any(len(line) > MAX_KEYWORD_LENGTH for line in lines):
            raise forms.ValidationError(f"Keep each keyword under {MAX_KEYWORD_LENGTH} characters.")
        return "\n".join(lines)

    def clean_exclude_keywords(self):
        return "\n".join(_clean_lines(self.cleaned_data.get("exclude_keywords")))

    def clean_page(self):
        page = self.cleaned_data.get("page")
        if page is not None and page.organization_id != self.organization.pk:
            raise forms.ValidationError("Choose one of your own Pages.")
        return page

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get("action")
        template = (cleaned.get("reply_template") or "").strip()
        if action == AutomationRule.Action.REPLY:
            if not template:
                self.add_error("reply_template", "Write the reply Replytic should post.")
            elif len(template) > settings.MAX_REPLY_LENGTH:
                self.add_error("reply_template", f"Keep replies under {settings.MAX_REPLY_LENGTH} characters.")
            else:
                unknown = sorted(set(engine.used_placeholders(template)) - set(engine.PLACEHOLDERS))
                if unknown:
                    names = ", ".join("{" + name + "}" for name in unknown)
                    self.add_error("reply_template", f"Unknown placeholder {names}. Use only: " + ", ".join("{" + p + "}" for p in engine.PLACEHOLDERS) + ".")
        else:
            cleaned["reply_template"] = ""
        cleaned["reply_template"] = (cleaned.get("reply_template") or "").strip()
        return cleaned


class PageModeForm(forms.Form):
    mode = forms.ChoiceField(choices=FacebookPage.AutomationMode.choices)
    confirm_auto = forms.BooleanField(required=False)
    max_auto_replies_per_hour = forms.IntegerField(min_value=1, max_value=200, required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mode") == FacebookPage.AutomationMode.AUTO and not cleaned.get("confirm_auto"):
            self.add_error("confirm_auto", "Confirm that you want Replytic to reply publicly without approval.")
        return cleaned


class TestCommentForm(BootstrapFormMixin, forms.Form):
    message = forms.CharField(label="Try a comment", max_length=1000, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "How much is the 3-seater sofa?"}))
    author_name = forms.CharField(label="Customer name", max_length=100, required=False, initial="Tendai Moyo")
