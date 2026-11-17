"""First Milk email renderer: Python standard library only, with no network I/O.

Render HTML and plain text for an existing mail provider. Rendering never sends
mail, issues an OTP, changes authentication, schedules delivery, or creates an
unsubscribe endpoint. Callers must supply accurate event data and working URLs.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from urllib.parse import urlsplit


ALLOWED_HOSTS = frozenset({
    "firstmilkventure.com",
    "www.firstmilkventure.com",
    "admin.firstmilkventure.com",
    "delivery.firstmilkventure.com",
    "backend.firstmilkventure.com",
})

KINDS = (
    "verification", "welcome", "order_confirmation",
    "subscription_confirmation", "payment_receipt", "delivery_confirmation",
    "password_reset", "security_notice", "driver_approval", "support_update",
    "offer",
)

_NAVY = "#112F38"
_EMERALD = "#08784E"
_GOLD = "#E2B863"
_CREAM = "#F7F5EC"
_INK = "#193C36"
_MUTED = "#536C68"
_GREEN_TINT = "#E8F4EC"
_GOLD_TINT = "#FFF3D8"

# Subjects deliberately contain no OTPs or caller-supplied header text.
_COPY = {
    "verification": {
        "subject": "Your First Milk verification code",
        "eyebrow": "ACCOUNT SECURITY", "title": "A fresh start, securely.",
        "intro": "Use this verification code to continue your First Milk request.",
        "panel_title": "Your one-time code", "accent": _EMERALD,
        "tint": _GREEN_TINT, "cta": "", "url": None,
        "preheader": "Your one-time verification code expires in 5 minutes.",
    },
    "welcome": {
        "subject": "Welcome to First Milk",
        "eyebrow": "WELCOME TO FIRST MILK", "title": "Fresh essentials. A lovely new routine.",
        "intro": "Welcome to First Milk. Explore the fresh essentials and delivery options available in your account.",
        "panel_title": "Your First Milk journey", "accent": _EMERALD,
        "tint": _GREEN_TINT, "cta": "Explore First Milk",
        "url": "https://www.firstmilkventure.com/",
        "preheader": "Welcome aboard. Your fresh routine starts here.",
    },
    "order_confirmation": {
        "subject": "Your First Milk order is confirmed",
        "eyebrow": "ORDER CONFIRMED", "title": "Fresh plans, confirmed.",
        "intro": "Your order is confirmed. Review the order and delivery details below.",
        "panel_title": "Order at a glance", "accent": _EMERALD,
        "tint": _GREEN_TINT, "cta": "View your order",
        "url": "https://www.firstmilkventure.com/",
        "preheader": "Your order confirmation and delivery details are inside.",
    },
    "subscription_confirmation": {
        "subject": "Your First Milk subscription is confirmed",
        "eyebrow": "SUBSCRIPTION CONFIRMED", "title": "Make freshness a habit.",
        "intro": "Your subscription is confirmed. Here is the delivery schedule recorded for your subscription.",
        "panel_title": "Your fresh routine", "accent": _GOLD,
        "tint": _GOLD_TINT, "cta": "Manage your subscription",
        "url": "https://www.firstmilkventure.com/",
        "preheader": "Your recurring delivery details, all in one place.",
    },
    "payment_receipt": {
        "subject": "Your First Milk payment receipt",
        "eyebrow": "PAYMENT RECEIVED", "title": "Payment received. Thank you.",
        "intro": "We have received your payment. Keep the details below for your records.",
        "panel_title": "Payment summary", "accent": _EMERALD,
        "tint": _GREEN_TINT, "cta": "View your account",
        "url": "https://www.firstmilkventure.com/",
        "preheader": "Your payment summary and reference details are inside.",
    },
    "delivery_confirmation": {
        "subject": "Your First Milk delivery is confirmed",
        "eyebrow": "DELIVERY CONFIRMED", "title": "Freshness has arrived.",
        "intro": "Your delivery has been marked complete. Review its details below; contact support if anything needs attention.",
        "panel_title": "Delivery details", "accent": _EMERALD,
        "tint": _GREEN_TINT, "cta": "View your delivery",
        "url": "https://www.firstmilkventure.com/",
        "preheader": "Your completed delivery details are ready to review.",
    },
    "password_reset": {
        "subject": "Your First Milk password reset code",
        "eyebrow": "PASSWORD RESET", "title": "A secure way back in.",
        "intro": "Use this code to continue the password reset you requested.",
        "panel_title": "Your password reset code", "accent": _GOLD,
        "tint": _GOLD_TINT, "cta": "", "url": None,
        "preheader": "Your password reset code expires in 5 minutes.",
    },
    "security_notice": {
        "subject": "A security update for your First Milk account",
        "eyebrow": "ACCOUNT SECURITY", "title": "Your account, kept in view.",
        "intro": "There is a security update for your First Milk account. Review the event details below.",
        "panel_title": "Security event", "accent": _GOLD,
        "tint": _GOLD_TINT, "cta": "Review your account",
        "url": "https://www.firstmilkventure.com/",
        "preheader": "Please review this update about your First Milk account.",
    },
    "driver_approval": {
        "subject": "Your First Milk driver account is approved",
        "eyebrow": "DRIVER ACCOUNT APPROVED", "title": "Ready for your next chapter.",
        "intro": "Your driver account has been approved. Sign in to review your profile and available work information.",
        "panel_title": "Your driver account", "accent": _EMERALD,
        "tint": _GREEN_TINT, "cta": "Open the driver portal",
        "url": "https://delivery.firstmilkventure.com/",
        "preheader": "Your driver account is approved. Sign in to continue.",
    },
    "support_update": {
        "subject": "An update on your First Milk support request",
        "eyebrow": "SUPPORT UPDATE", "title": "We are here to help.",
        "intro": "There is an update on your support request. Review the latest information below.",
        "panel_title": "Your support request", "accent": _EMERALD,
        "tint": _GREEN_TINT, "cta": "View your support request",
        "url": "https://www.firstmilkventure.com/",
        "preheader": "The latest information about your support request is inside.",
    },
    "offer": {
        "subject": "A fresh offer from First Milk",
        "eyebrow": "SOMETHING FRESH FOR YOU", "title": "",
        "intro": "", "panel_title": "Offer details", "accent": _GOLD,
        "tint": _GOLD_TINT, "cta": "Explore the offer",
        "url": "https://www.firstmilkventure.com/",
        "preheader": "Explore the offer details, eligibility and terms inside.",
    },
}


# v9 purpose-specific variants preserve every original renderer variant above.
for _kind, _subject, _purpose, _title, _intro, _base in (
    ('signup_verification','First Milk — sign-up verification code','Sign up','Verify your new account.','Use this code to finish creating your First Milk account.','verification'),
    ('signin_verification','First Milk — sign-in verification code','Sign in','Confirm your sign-in.','Use this code to complete your First Milk sign-in.','verification'),
    ('admin_invitation','First Milk — administrator invitation','Administrator invitation','Your workspace invitation.','You have been invited to join the First Milk administration workspace.','welcome'),
    ('subscription_created','First Milk — subscription started','Subscription started','Your fresh routine is scheduled.','Your subscription has been created. Review your delivery schedule below.','subscription_confirmation'),
    ('subscription_paused','First Milk — subscription paused','Subscription paused','Your deliveries are paused.','Your subscription is paused. Resume it from My Orders when you are ready.','subscription_confirmation'),
    ('subscription_resumed','First Milk — subscription resumed','Subscription resumed','Your fresh routine is back.','Your subscription has resumed. Future deliveries follow the schedule below.','subscription_confirmation'),
    ('subscription_cancelled','First Milk — subscription cancelled','Subscription cancelled','Your subscription is cancelled.','Future deliveries for this subscription have been stopped. Existing orders retain their recorded status.','subscription_confirmation'),
    ('order_placed','First Milk — order placed','Order placed','Your order is placed.','We have recorded your order. Review the delivery and payment details below.','order_confirmation'),
    ('order_shipped','First Milk — your order has shipped','Order shipped','Your order is on its way.','Your order has been dispatched. Follow its progress in My Orders.','order_confirmation'),
    ('order_out_for_delivery','First Milk — out for delivery','Out for delivery','Next stop: your doorstep.','Your order is out for delivery. Keep your delivery PIN ready.','order_confirmation'),
    ('order_cancelled','First Milk — order cancelled','Order cancelled','Your order has been cancelled.','Your order is cancelled. Review the order details below.','order_confirmation'),
    ('cash_receipt','First Milk — accounts received your cash','Cash received by accounts','Your cash handover is recorded.','Accounts has confirmed receipt of your cash. Your driver balance has been updated.','payment_receipt'),
    ('order_delivered','First Milk — order delivered','Order delivered','Delivered. Thank you!','Your driver has confirmed delivery using your delivery PIN and proof of delivery.','delivery_confirmation'),
):
    _COPY[_kind] = dict(_COPY[_base],subject=_subject,purpose=_purpose,eyebrow=_purpose.upper(),title=_title,intro=_intro,preheader=_intro)
KINDS = tuple(_COPY)


def _value(value: object) -> str:
    return "" if value is None else str(value)


def _escape(value: object) -> str:
    return html.escape(_value(value), quote=True)


def _paragraph(value: object) -> str:
    return _escape(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _url(value: object, field: str) -> str:
    """Permit only HTTPS URLs on the known First Milk domains.

    A permitted host does not prove that the path exists or that a preference
    endpoint honors unsubscribe requests. Those remain caller responsibilities.
    """
    if not isinstance(value, str) or not value or re.search(r"[\x00-\x20\x7f\\]", value):
        raise ValueError(f"{field} must be a valid HTTPS First Milk URL")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in ALLOWED_HOSTS
            and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{field} must use HTTPS on an allowed First Milk host, without user information or fragments")
    return value


def _details(details: Iterable[tuple[object, object]]) -> list[tuple[str, str]]:
    if isinstance(details, (str, bytes, dict)):
        raise ValueError("details must be a sequence of (label, value) pairs")
    result = []
    try:
        for pair in details:
            if isinstance(pair, (str, bytes, dict)):
                raise ValueError("details must contain (label, value) pairs")
            label, value = pair
            result.append((_value(label), _value(value)))
    except (TypeError, ValueError) as error:
        raise ValueError("details must contain (label, value) pairs") from error
    return result


def build_email(
    kind: str,
    *,
    name: str = "",
    code: str = "",
    details: Iterable[tuple[object, object]] = (),
    message: str = "",
    action_url: str | None = None,
    offer_title: str = "",
    offer_description: str = "",
    offer_terms: str = "",
    unsubscribe_url: str | None = None,
    company_ad: dict | None = None,
) -> dict[str, str]:
    """Return subject/html/text for one of KINDS, without sending anything.

    Verification and password-reset codes must be six ASCII digits issued by the
    caller's existing authentication flow; this renderer neither generates nor
    validates codes against a database. Its displayed expiry is five minutes.

    details is a sequence of label/value pairs, for example
    (("Order", "FMV-123"), ("Total", "INR 120.00")). All supplied content is
    treated as text and HTML-escaped. Pass accurate values from a verified event.

    Offers require a title, description, terms and a working unsubscribe_url on
    an allowed First Milk host. No unsubscribe endpoint is created by rendering.
    action_url=None uses the variant's public portal link; OTP variants have no
    button by default. Supplying a URL overrides that link after validation.
    """
    if kind not in _COPY:
        raise ValueError("Unknown email kind. Choose one of: " + ", ".join(KINDS))
    content = _COPY[kind]
    is_otp = kind in ("verification", "password_reset", "signup_verification", "signin_verification")
    if is_otp and (not isinstance(code, str) or re.fullmatch(r"[0-9]{6}", code) is None):
        raise ValueError("code must contain exactly six ASCII digits")
    rows = _details(details)
    rows.insert(0, ("Purpose", content.get("purpose", content["eyebrow"].title())))
    if kind == "offer":
        if not all(isinstance(value, str) and value.strip() for value in (offer_title, offer_description, offer_terms)):
            raise ValueError("offer requires a title, description and terms")
        if not unsubscribe_url:
            raise ValueError("offer requires a working unsubscribe_url")
    unsubscribe = _url(unsubscribe_url, "unsubscribe_url") if unsubscribe_url is not None else None
    selected_url = action_url if action_url is not None else content["url"]
    action = _url(selected_url, "action_url") if selected_url is not None else None
    title = offer_title if kind == "offer" else content["title"]
    intro = offer_description if kind == "offer" else content["intro"]
    greeting = "Hello " + _value(name).strip() + "," if _value(name).strip() else "Hello,"
    accent, tint = content["accent"], content["tint"]

    detail_html = "".join(
        '<tr><td class="detail-label" width="42%" valign="top" style="padding:10px 12px 10px 0;border-bottom:1px solid #D4E4D9;font:14px/21px Arial,sans-serif;color:' + _MUTED + ';overflow-wrap:anywhere;">'
        + _paragraph(label) + '</td><td valign="top" style="padding:10px 0;border-bottom:1px solid #D4E4D9;font:600 14px/21px Arial,sans-serif;color:' + _INK + ';overflow-wrap:anywhere;word-break:break-word;">'
        + _paragraph(value) + '</td></tr>'
        for label, value in rows
    )
    details_block = (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;border-collapse:collapse;">' + detail_html + '</table>'
        if rows else ""
    )
    code_block = ""
    if is_otp:
        code_block = (
            '<p style="margin:12px 0 8px;font:700 36px/48px Courier New,monospace;letter-spacing:0;user-select:all;-webkit-user-select:all;white-space:nowrap;color:' + _NAVY + ';">' + _escape(code) + '</p>'
            '<p style="margin:0;font:14px/22px Arial,sans-serif;color:' + _MUTED + ';">Expires in <strong>5 minutes</strong>. Use this code once. Never share it with anyone.</p>'
        )
    message_block = (
        '<p style="margin:20px 0 0;font:16px/26px Arial,sans-serif;color:' + _INK + ';overflow-wrap:anywhere;">' + _paragraph(message) + '</p>'
        if _value(message).strip() else ""
    )
    panel = ""
    if is_otp or rows or kind in ("welcome", "offer"):
        extra = ""
        if kind == "welcome" and not rows:
            extra = '<p style="margin:10px 0 0;font:15px/24px Arial,sans-serif;color:' + _INK + ';">Explore the catalog. Review delivery options. Keep your account details up to date.</p>'
        if kind == "offer":
            extra = '<p style="margin:12px 0 0;font:700 12px/18px Arial,sans-serif;letter-spacing:1px;color:' + _INK + ';">TERMS &amp; ELIGIBILITY</p><p style="margin:6px 0 0;font:14px/22px Arial,sans-serif;color:' + _MUTED + ';overflow-wrap:anywhere;">' + _paragraph(offer_terms) + '</p>'
        panel = (
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="' + tint + '" style="width:100%;background-color:' + tint + ';border-left:4px solid ' + accent + ';border-radius:10px;margin-top:24px;"><tr><td style="padding:20px;">'
            '<p style="margin:0;font:700 12px/19px Arial,sans-serif;letter-spacing:1px;color:' + _INK + ';">' + _escape(content["panel_title"].upper()) + '</p>'
            + code_block + details_block + extra + '</td></tr></table>'
        )
    button = ""
    if action:
        label = content["cta"] or "Open First Milk"
        button = (
            '<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin-top:26px;"><tr><td align="center" bgcolor="' + _EMERALD + '" style="background-color:' + _EMERALD + ';border-radius:7px;mso-padding-alt:14px 24px;">'
            '<a href="' + _escape(action) + '" style="display:inline-block;padding:14px 24px;border:1px solid ' + _EMERALD + ';border-radius:7px;color:#FFFFFF;font:700 15px/22px Arial,sans-serif;text-align:center;text-decoration:none;mso-padding-alt:0;">' + _escape(label) + '</a>'
            '</td></tr></table>'
        )
    safety_note = (
        "If you did not request this code, you can ignore this email. Your password will not change unless you complete the reset."
        if kind == "password_reset" else
        "If you did not request this code, you can ignore this email."
        if is_otp else
        "If you do not recognize this activity, open First Milk directly and contact support."
        if kind == "security_notice" else
        "Need help? Open your First Milk portal to contact support."
    )
    unsubscribe_block = (
        '<p style="margin:12px 0 0;font:12px/20px Arial,sans-serif;color:#D5E6DF;"><a href="' + _escape(unsubscribe) + '" style="color:#FFFFFF;text-decoration:underline;">Unsubscribe from promotional emails</a></p>'
        if unsubscribe else ""
    )
    ad = company_ad or {}
    ad_title = _value(ad.get('title') or 'Fresh essentials. Every day with FMV.')
    ad_description = _value(ad.get('description') or 'Explore First Milk Venture for milk, ghee, curd and butter. Shop your favourites or plan a fresh daily routine.')
    ad_url = _url(ad.get('action_url') or 'https://www.firstmilkventure.com/', 'company ad link')
    ad_image = _url(ad['image_url'], 'company ad image') if ad.get('image_url') else ''
    ad_block = ('<table role="presentation" width="100%" style="margin-top:26px;background:#E8F4EC;border-radius:12px"><tr><td style="padding:20px;">'
                + '<p style="font:700 11px Arial;color:#08784E;letter-spacing:1px;">FROM FIRST MILK VENTURE</p>'
                + ('<img src="'+_escape(ad_image)+'" alt="'+_escape(ad_title)+'" width="480" style="width:100%;max-width:480px;height:auto;border-radius:8px;">' if ad_image else '')
                + '<h2 style="font:700 20px/28px Arial;color:#112F38;">'+_escape(ad_title)+'</h2>'
                + '<p style="font:14px/22px Arial;color:#536C68;">'+_paragraph(ad_description)+'</p>'
                + '<a href="'+_escape(ad_url)+'" style="font:700 14px Arial;color:#08784E;">Discover First Milk →</a></td></tr></table>')
    html_body = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{_escape(content["subject"])}</title>
<style>@media only screen and (max-width:620px){{.email-shell{{width:100%!important;max-width:100%!important}}.outer-pad{{padding:12px 8px!important}}.content-pad{{padding:26px 22px!important}}.email-title{{font-size:29px!important;line-height:36px!important}}}}</style></head>
<body bgcolor="{_CREAM}" style="margin:0;padding:0;background-color:{_CREAM};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;font-size:1px;line-height:1px;color:{_CREAM};mso-hide:all;">{_escape(content["preheader"])}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="{_CREAM}" style="width:100%;background-color:{_CREAM};border-collapse:collapse;"><tr><td class="outer-pad" align="center" style="padding:32px 12px;">
<table class="email-shell" role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="width:600px;max-width:100%;border-collapse:collapse;">
<tr><td bgcolor="{_NAVY}" style="padding:24px 30px;background-color:{_NAVY};border-radius:16px 16px 0 0;border-bottom:4px solid {_GOLD};">
<p style="margin:0;font:700 22px/28px Arial,sans-serif;letter-spacing:1px;color:#FFFFFF;">FIRST MILK<span style="color:{_GOLD};">.</span></p><p style="margin:5px 0 0;font:11px/18px Arial,sans-serif;letter-spacing:2px;color:#D5E6DF;">VENTURE &nbsp; / &nbsp; FRESH EVERYDAY</p></td></tr>
<tr><td class="content-pad" bgcolor="#FFFFFF" style="padding:34px 32px;background-color:#FFFFFF;">
<p style="margin:0 0 12px;font:700 11px/18px Arial,sans-serif;letter-spacing:1.5px;color:{_EMERALD};">{_escape(content["eyebrow"])}</p>
<h1 class="email-title" style="margin:0 0 22px;font:700 34px/41px Georgia,serif;color:{_NAVY};">{_escape(title)}</h1>
<p style="margin:0 0 10px;font:600 16px/25px Arial,sans-serif;color:{_INK};">{_escape(greeting)}</p>
<p style="margin:0;font:16px/26px Arial,sans-serif;color:{_MUTED};">{_paragraph(intro)}</p>
{panel}{message_block}{button}{ad_block}
<p style="margin:26px 0 0;padding-top:20px;border-top:1px solid #E1EAE3;font:13px/21px Arial,sans-serif;color:{_MUTED};">{_escape(safety_note)}</p>
</td></tr>
<tr><td bgcolor="{_NAVY}" style="padding:23px 30px;background-color:{_NAVY};border-radius:0 0 16px 16px;">
<p style="margin:0;font:700 13px/21px Arial,sans-serif;color:#FFFFFF;">First Milk Venture</p><p style="margin:4px 0 0;font:12px/20px Arial,sans-serif;color:#D5E6DF;">Fresh essentials. Thoughtfully connected.</p>{unsubscribe_block}</td></tr>
</table></td></tr></table></body></html>'''

    plain = ["FIRST MILK VENTURE", "", title, "", greeting, intro]
    if is_otp:
        plain = [code + " is your First Milk verification code.", ""] + plain
        plain += ["", content["panel_title"] + ": " + code,
                  "Expires in 5 minutes. Use this code once. Never share it with anyone."]
    if rows:
        plain += [""] + [f"{label}: {value}" for label, value in rows]
    if kind == "welcome" and not rows:
        plain += ["", "Explore the catalog. Review delivery options. Keep your account details up to date."]
    if kind == "offer":
        plain += ["", "Terms & eligibility: " + offer_terms]
    if _value(message).strip():
        plain += ["", _value(message)]
    if action:
        plain += ["", (content["cta"] or "Open First Milk") + ": " + action]
    plain += ["", safety_note, "", "First Milk Venture"]
    plain += ["", "FROM FIRST MILK VENTURE", ad_title, ad_description, "Discover First Milk: " + ad_url]
    if unsubscribe:
        plain += ["Unsubscribe from promotional emails: " + unsubscribe]
    return {"subject": content["subject"], "html": html_body, "text": "\n".join(plain)}
