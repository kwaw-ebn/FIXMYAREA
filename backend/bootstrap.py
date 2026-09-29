"""Trusted operator script: create an organization and its first administrator."""
import getpass
from argon2 import PasswordHasher
from sqlalchemy import select
from app import Session, Organization, User

name=input('Organization name: ').strip()
district=input('District: ').strip()
email=input('Administrator email: ').strip().lower()
password=getpass.getpass('Administrator password (at least 10 characters): ')
if not name or not district or '@' not in email or len(password)<10:
    raise SystemExit('Invalid input')
with Session.begin() as session:
    if session.scalar(select(User).where(User.email==email)):
        raise SystemExit('Email already registered')
    org=Organization(name=name,district=district)
    session.add(org); session.flush()
    session.add(User(email=email,password=PasswordHasher().hash(password),role='org_admin',organization_id=org.id))
print('Organization and administrator created')
