"""First-administrator invitation configured privately by the hosting owner.

This only creates an invitation. Normal password validation, email OTP and
single-use invitation checks still apply. No account or session is provisioned.
"""
import hashlib
import logging
import os
import time

from pydantic import EmailStr, TypeAdapter, ValidationError


def configure_first_admin_invitation(state, environ=None):
    """Call inside the existing database transaction during application startup."""
    settings=os.environ if environ is None else environ
    email=settings.get('BOOTSTRAP_ADMIN_EMAIL','').strip().lower()
    invitation=settings.get('BOOTSTRAP_ADMIN_INVITATION','').strip()
    if not email and not invitation:
        return 'disabled'
    if any(user.get('portal')=='admin' for user in state['users'].values()):
        return 'already_configured'
    if not email or not invitation:
        logging.getLogger(__name__).warning('First-admin setup skipped: set both BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_INVITATION.')
        return 'incomplete'
    try:
        email=str(TypeAdapter(EmailStr).validate_python(email)).lower()
    except ValidationError:
        logging.getLogger(__name__).warning('First-admin setup skipped: BOOTSTRAP_ADMIN_EMAIL must be a valid email address.')
        return 'invalid'
    if not 32<=len(invitation)<=200 or any(char.isspace() for char in invitation):
        logging.getLogger(__name__).warning('First-admin setup skipped: use a private invitation of 32–200 characters without spaces.')
        return 'invalid'
    digest=hashlib.sha256(invitation.encode()).hexdigest()
    previous=state.get('first_admin_bootstrap',{})
    if previous.get('digest')==digest:
        record=state['invites'].get(digest)
        if not record or record.get('used'):
            return 'used'
        if record['email']!=email:
            logging.getLogger(__name__).warning('First-admin setup skipped: use a new invitation when changing the invited email.')
            return 'email_changed'
        # A restart never extends the original 24-hour invitation lifetime.
        return 'ready' if record['expires']>time.time() else 'expired'
    # Do not repurpose a code that was issued through another invitation flow.
    if digest in state['invites']:
        return 'code_already_used'
    if previous.get('digest') in state['invites']:
        state['invites'][previous['digest']]['used']=True
    stamp=time.time()
    state['invites'][digest]=dict(email=email,role='super_admin',expires=stamp+86400,
                                 used=False,created_by='hosting-owner-bootstrap')
    state['first_admin_bootstrap']=dict(digest=digest,created_at=stamp)
    logging.getLogger(__name__).info('First-admin invitation is ready. Complete admin signup and email verification; remove bootstrap variables afterwards.')
    return 'ready'
