"""First-account setup never bypasses email OTP or exposes an invitation."""
import hashlib
import time

from fastapi.testclient import TestClient
from main import app
import secure_backend as sec
from bootstrap_admin import configure_first_admin_invitation
from test_secure_flows import signup, verify

client=TestClient(app)


def settings(email='owner@example.com',suffix='a'):
    return {'BOOTSTRAP_ADMIN_EMAIL':email,
            'BOOTSTRAP_ADMIN_INVITATION':'isolated-test-invitation-'+suffix*24}


def test_disabled_or_partial_setup_creates_nothing():
    state=sec.initial_state()
    assert configure_first_admin_invitation(state,{})=='disabled'
    assert configure_first_admin_invitation(state,{'BOOTSTRAP_ADMIN_EMAIL':'owner@example.com'})=='incomplete'
    assert not state['invites'] and not state['users']


def test_private_invitation_is_hashed_and_never_logged(caplog):
    state=sec.initial_state();env=settings();token=env['BOOTSTRAP_ADMIN_INVITATION']
    with caplog.at_level('INFO'):
        assert configure_first_admin_invitation(state,env)=='ready'
    digest=hashlib.sha256(token.encode()).hexdigest()
    assert state['invites'][digest]['email']=='owner@example.com'
    assert state['invites'][digest]['role']=='super_admin'
    assert token not in repr(state) and token not in caplog.text
    assert not state['users'] and not state['sessions']


def test_restarts_do_not_extend_expiry_or_recreate_used_codes():
    state=sec.initial_state();env=settings();digest=hashlib.sha256(env['BOOTSTRAP_ADMIN_INVITATION'].encode()).hexdigest()
    assert configure_first_admin_invitation(state,env)=='ready'
    expiry=state['invites'][digest]['expires']
    assert configure_first_admin_invitation(state,env)=='ready'
    assert state['invites'][digest]['expires']==expiry
    state['invites'][digest]['expires']=time.time()-1
    assert configure_first_admin_invitation(state,env)=='expired'
    state['invites'][digest]['used']=True
    assert configure_first_admin_invitation(state,env)=='used'


def test_existing_administrator_blocks_bootstrap_without_promoting_anyone():
    state=sec.initial_state()
    state['users']['admin:staff@example.com']={'portal':'admin','role':'customer_support'}
    assert configure_first_admin_invitation(state,settings())=='already_configured'
    assert not state['invites']
    assert state['users']['admin:staff@example.com']['role']=='customer_support'


def test_new_private_code_replaces_old_code_without_extending_it():
    state=sec.initial_state();old=settings();new=settings(suffix='b')
    assert configure_first_admin_invitation(state,old)=='ready'
    assert configure_first_admin_invitation(state,new)=='ready'
    old_digest=hashlib.sha256(old['BOOTSTRAP_ADMIN_INVITATION'].encode()).hexdigest()
    assert state['invites'][old_digest]['used']
    assert len(state['invites'])==2


def test_invalid_email_weak_or_spaced_code_is_rejected_without_logging_secret(caplog):
    for env in [settings(email='invalid'),{**settings(),'BOOTSTRAP_ADMIN_INVITATION':'short'},
                {**settings(),'BOOTSTRAP_ADMIN_INVITATION':'this has spaces and is long enough to fail'}]:
        state=sec.initial_state()
        assert configure_first_admin_invitation(state,env)=='invalid'
        assert not state['invites']
        assert env['BOOTSTRAP_ADMIN_INVITATION'] not in caplog.text


def test_changing_email_requires_new_private_code():
    state=sec.initial_state();env=settings()
    assert configure_first_admin_invitation(state,env)=='ready'
    assert configure_first_admin_invitation(state,settings(email='other@example.com'))=='email_changed'
    assert next(iter(state['invites'].values()))['email']=='owner@example.com'


def test_bootstrap_invitation_requires_named_email_and_successful_otp():
    env=settings()
    with sec.transaction() as state:
        assert configure_first_admin_invitation(state,env)=='ready'
    token=env['BOOTSTRAP_ADMIN_INVITATION']
    assert signup('other@example.com','admin','9876543211',token).status_code==403
    response=signup('owner@example.com','admin','9876543210',token)
    assert response.status_code==200 and 'access_token' not in response.json()
    with sec.transaction() as state:
        assert not state['users']
    verified=verify(response)
    assert verified.status_code==200
    assert verified.json()['user']['role']=='super_admin'
    with sec.transaction() as state:
        assert configure_first_admin_invitation(state,settings(email='another@example.com',suffix='c'))=='already_configured'
    assert signup('owner@example.com','admin','9876543210',token).status_code==409


def test_normal_backend_start_reads_private_host_settings(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    env=dict(os.environ)
    env.update(settings(),DATABASE_URL='',FMV_DB_PATH=str(tmp_path/'startup.sqlite3'),APP_ENV='test')
    script='''
import sys, os, hashlib
sys.path.insert(0,sys.argv[1])
import dotenv
dotenv.load_dotenv=lambda *args,**kwargs:False
import main
import secure_backend as sec
with sec.transaction() as state:
    digest=hashlib.sha256(os.environ['BOOTSTRAP_ADMIN_INVITATION'].encode()).hexdigest()
    assert state['invites'][digest]['role']=='super_admin'
    assert state['invites'][digest]['email']==os.environ['BOOTSTRAP_ADMIN_EMAIL']
    assert not state['users'] and not state['sessions']
print('startup-invitation-ready')
'''
    result=subprocess.run([sys.executable,'-I','-c',script,str(Path(__file__).resolve().parent)],
                          env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'startup-invitation-ready' in result.stdout
    assert env['BOOTSTRAP_ADMIN_INVITATION'] not in result.stdout+result.stderr
