"""Branding regression: first-party official logo in operational email HTML."""
from email_templates import build_email


def test_fmv_identity_in_verification_email():
    mail=build_email('signin_verification', code='123456')
    html=mail['html']
    assert 'https://customer.firstmilkventure.com/assets/brand/fmv-icon.png' in html
    assert 'alt="First Milk Venture official logo"' in html
    assert 'FIRST MILK' in html
    assert '123456' in mail['text']
    assert '<script' not in html.lower()
    assert 'media.canva.com' not in html
    assert 'https://customer.firstmilkventure.com/assets/brand/' in html


def test_fmv_identity_in_delivery_or_finance_email():
    mail=build_email('order_confirmation',name='Customer')
    html=mail['html']
    assert html.count('fmv-icon.png')==1
    assert 'First Milk Venture' in mail['text']
    assert '64px' in html
