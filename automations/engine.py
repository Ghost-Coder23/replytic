"""
The rule engine. Pure functions with no network access, so it is easy to test.

Matching is case-insensitive and whole-word/phrase: the keyword "price" matches
"What is the PRICE?" but not "priceless". Keywords are entered by the business,
so any language works.
"""
import re
from dataclasses import dataclass
from typing import Optional

PLACEHOLDERS = {
    "first_name": "The commenter's first name",
    "page_name": "Your Facebook Page name",
    "business_name": "Your business name",
    "phone": "Your business phone number",
    "whatsapp": "Your WhatsApp number",
}
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def normalize(text):
    return re.sub(r"\s+", " ", (text or "").translate(_QUOTES).casefold()).strip()


def keyword_in(text_normalized, keyword):
    keyword = normalize(keyword)
    if not keyword:
        return False
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text_normalized) is not None


def rule_matches(rule, message):
    text = normalize(message)
    if any(keyword_in(text, word) for word in rule.exclude_list):
        return False
    keywords = rule.keyword_list
    if not keywords:
        return False
    checks = (keyword_in(text, word) for word in keywords)
    return all(checks) if rule.match_mode == "ALL" else any(checks)


def used_placeholders(template):
    return _PLACEHOLDER_RE.findall(template or "")


def first_name_of(author_name):
    parts = (author_name or "").split()
    return parts[0] if parts else "there"


def render_template(template, *, comment_author, page_name, organization):
    """Fill placeholders. Returns (text, missing) where missing lists empty/unknown ones."""
    values = {
        "first_name": first_name_of(comment_author),
        "page_name": page_name or "",
        "business_name": organization.name,
        "phone": organization.phone,
        "whatsapp": organization.whatsapp_number,
    }
    missing = []

    def substitute(match):
        key = match.group(1)
        value = values.get(key, "")
        if not value:
            missing.append(key)
            return match.group(0)
        return value

    return _PLACEHOLDER_RE.sub(substitute, template or ""), missing


@dataclass
class Decision:
    rule: object
    action: str
    text: str = ""
    missing: tuple = ()


def decide(*, message, author_name, page, organization, rules) -> Optional[Decision]:
    """First matching active rule (by priority) for this Page, or None."""
    for rule in sorted(rules, key=lambda r: (r.priority, r.created_at)):
        if not rule.is_active or rule.organization_id != organization.pk:
            continue
        if rule.page_id and (page is None or rule.page_id != page.pk):
            continue
        if not rule_matches(rule, message):
            continue
        if rule.action != "REPLY":
            return Decision(rule=rule, action=rule.action)
        text, missing = render_template(
            rule.reply_template, comment_author=author_name,
            page_name=page.name if page else "", organization=organization,
        )
        return Decision(rule=rule, action="REPLY", text=text, missing=tuple(missing))
    return None
