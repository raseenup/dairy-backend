"""Isolated tests: no real email or financial requests are sent."""
import os
import tempfile
os.environ['JWT_SECRET']='isolated-test-signing-secret-32-characters-minimum'
os.environ['FMV_DB_PATH']=tempfile.mkdtemp(prefix='fmv-tests-')+'/application.sqlite3'
os.environ['APP_ENV']='test'
# Never inherit a configured PostgreSQL URL or load a real .env during tests.
os.environ['DATABASE_URL']=''
import dotenv
dotenv.load_dotenv=lambda *args,**kwargs: False
os.environ.pop('RAZORPAY_WEBHOOK_SECRET',None)
os.environ.pop('BOOTSTRAP_ADMIN_EMAIL',None)
os.environ.pop('BOOTSTRAP_ADMIN_INVITATION',None)
import pytest
import secure_backend as secure

@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    with secure.transaction() as state:
        state.clear();state.update(secure.initial_state())
    monkeypatch.setattr(secure,'send_code',lambda email,code,*args,**kwargs:None)
    monkeypatch.setattr(secure,'rate_limit',lambda *a,**kw:None)
