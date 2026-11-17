"""Purpose-specific verified senders. Non-auth mail never falls back to auth@."""
import os
from email.utils import parseaddr
from fastapi import HTTPException

SENDERS = {
    'auth': ('AUTH_EMAIL_SENDER', 'First Milk Auth <auth@firstmilkventure.com>'),
    'orders': ('ORDER_EMAIL_SENDER', 'First Milk Orders <orders@firstmilkventure.com>'),
    'delivery': ('DELIVERY_EMAIL_SENDER', 'First Milk Delivery <delivery@firstmilkventure.com>'),
    'drivers': ('DRIVER_EMAIL_SENDER', 'First Milk Drivers <drivers@firstmilkventure.com>'),
    'admin': ('ADMIN_EMAIL_SENDER', 'First Milk Administration <admin@firstmilkventure.com>'),
    'finance': ('FINANCE_EMAIL_SENDER', 'First Milk Accounts <accounts@firstmilkventure.com>'),
    'support': ('SUPPORT_EMAIL_SENDER', 'First Milk Support <support@firstmilkventure.com>'),
    'marketing': ('MARKETING_EMAIL_SENDER', 'First Milk Offers <offers@firstmilkventure.com>'),
    'security': ('SECURITY_EMAIL_SENDER', 'First Milk Security <security@firstmilkventure.com>'),
}


def category(kind):
    if kind in {'verification', 'signup_verification', 'signin_verification', 'password_reset'}:
        return 'auth'
    if kind in {'order_shipped', 'order_out_for_delivery', 'order_delivered', 'delivery_confirmation'}:
        return 'delivery'
    if kind.startswith('order_') or kind.startswith('subscription_'):
        return 'orders'
    if kind in {'driver_approval', 'driver_application'}:
        return 'drivers'
    if kind == 'admin_invitation':
        return 'admin'
    if kind in {'cash_receipt', 'payment_receipt'}:
        return 'finance'
    if kind == 'support_update':
        return 'support'
    if kind == 'offer':
        return 'marketing'
    if kind == 'security_notice':
        return 'security'
    return 'support'


def sender_for(kind):
    group = category(kind)
    variable, default = SENDERS[group]
    sender = os.getenv(variable, '').strip() or default
    _, address = parseaddr(sender)
    if '\n' in sender or '\r' in sender or '@' not in address:
        raise HTTPException(503, 'Configure a valid ' + variable + ' on the backend.')
    auth_address = parseaddr(os.getenv('AUTH_EMAIL_SENDER') or SENDERS['auth'][1])[1].lower()
    if group != 'auth' and address.lower() in {auth_address, 'auth@firstmilkventure.com'}:
        # The v9 example explicitly used auth@ for orders. Upgrade that legacy
        # value to the department default instead of continuing the misrouting.
        sender = default
    return sender
