"""New password policy must be consistent with frontend and not break old sign-ins."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
import secure_backend as sec

@pytest.mark.parametrize('password',[
    'Aa1!abcdef',              # exactly 10 characters
    'ComplexPass2026#',
    'O7#lowercase',
])
def test_new_password_policy_accepts_mixed_case_digit_and_symbol(password):
    sec.valid_password(password)
    assert sec.Signup(email='customer@example.com',phone='9876543210',
        full_name='Customer',address='Kerala',password=password).password==password

@pytest.mark.parametrize('password',[
    'Aa1!abcde',               # only 9 characters
    'alllowercase1!',
    'ALLUPPERCASE1!',
    'NoNumbers!!',
    'NoSymbols123',
    'A1!      a'[:9],
    'Aa1!'+'a'*125,
])
def test_new_password_policy_rejects_missing_classes_and_invalid_length(password):
    with pytest.raises(HTTPException) as exc:
        sec.valid_password(password)
    assert exc.value.status_code==422

def test_signup_schema_uses_new_minimum():
    with pytest.raises(ValidationError):
        sec.Signup(email='customer@example.com',phone='9876543210',
            full_name='Customer',address='Kerala',password='Aa1!abcde')

def test_existing_accounts_can_still_sign_in_with_original_password():
    # Existing password records are not migrated or rejected for missing classes.
    legacy_password='oldformat123'
    encoded=sec.password_hash(legacy_password)
    assert sec.password_ok(legacy_password,encoded)
    assert sec.Signin(identifier='customer@example.com',password=legacy_password).password==legacy_password
