import hashlib
import io
import os
import uuid
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

import jwt
from argon2 import PasswordHasher
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

DB = os.getenv('DATABASE_URL', 'sqlite:///./fixmyarea.db').replace('postgres://', 'postgresql+psycopg://').replace('postgresql://', 'postgresql+psycopg://')
SECRET = os.getenv('JWT_SECRET', 'local-development-only')
if os.getenv('RENDER') and SECRET == 'local-development-only':
    raise RuntimeError('JWT_SECRET must be configured')
engine = create_engine(DB, pool_pre_ping=True, connect_args={'check_same_thread': False} if DB.startswith('sqlite') else {})
Session = sessionmaker(engine)
class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default='citizen')
    organization_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

class Organization(Base):
    __tablename__ = 'organizations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(150))
    district: Mapped[str] = mapped_column(String(150))

class Report(Base):
    __tablename__ = 'reports'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tracking: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    reporter_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    organization_id: Mapped[str | None] = mapped_column(ForeignKey('organizations.id'), nullable=True)
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    category: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    community: Mapped[str] = mapped_column(String(120))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default='Submitted')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Photo(Base):
    __tablename__ = 'photos'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    report_id: Mapped[str] = mapped_column(ForeignKey('reports.id'))
    kind: Mapped[str] = mapped_column(String(15))
    data: Mapped[bytes] = mapped_column(LargeBinary)

class Event(Base):
    __tablename__ = 'events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    report_id: Mapped[str] = mapped_column(ForeignKey('reports.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    action: Mapped[str] = mapped_column(String(70))
    note: Mapped[str] = mapped_column(Text, default='')
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Support(Base):
    __tablename__ = 'supports'
    report_id: Mapped[str] = mapped_column(ForeignKey('reports.id'), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)

class Notice(Base):
    __tablename__ = 'notices'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    message: Mapped[str] = mapped_column(String(250))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(engine)
app = FastAPI(title='FixMyArea API')
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in os.getenv('FRONTEND_ORIGINS', 'http://localhost:5173').split(',')], allow_origin_regex=r'https://fixmyarea-web(?:-[a-z0-9-]+)?\.onrender\.com', allow_credentials=False, allow_methods=['GET','POST'], allow_headers=['Authorization','Content-Type'])
hasher = PasswordHasher()
CATEGORIES = ['Road/Pothole','Drainage','Waste/Sanitation','Streetlight','Water','Flooding','Public Facility','Road Safety','Environmental Hazard','Other']
STATES = ['Submitted','Under Review','Verified','Assigned','In Progress','Resolved','Closed']

def db():
    with Session() as session: yield session

def actor(authorization: str = Header(default=''), session=Depends(db)):
    try:
        token = authorization.removeprefix('Bearer ')
        uid = jwt.decode(token, SECRET, algorithms=['HS256'])['sub']
        user = session.get(User, uid)
        if user: return user
    except (jwt.PyJWTError, KeyError): pass
    raise HTTPException(401, 'Sign in required')

def staff(user):
    if user.role not in ('officer','supervisor','org_admin','platform_admin') or not user.organization_id and user.role != 'platform_admin':
        raise HTTPException(403, 'Organization access required')

def accessible(report, user):
    staff(user)
    if user.role != 'platform_admin' and report.organization_id != user.organization_id:
        raise HTTPException(403, 'Outside your organization')

def public(report, session):
    return dict(id=report.id, tracking=report.tracking, category=report.category, description=report.description, community=report.community, latitude=report.latitude, longitude=report.longitude, status=report.status, created_at=report.created_at.isoformat(), organization_id=report.organization_id, support_count=session.scalar(select(func.count()).select_from(Support).where(Support.report_id == report.id)) or 0, images=[{'id':p.id,'kind':p.kind} for p in session.scalars(select(Photo).where(Photo.report_id == report.id))])

class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)

@app.get('/health')
def health(): return {'status':'ok'}

@app.post('/api/auth/register')
def register(body: Credentials, session=Depends(db)):
    if session.scalar(select(User).where(User.email == body.email.lower())): raise HTTPException(409, 'Email registered')
    user = User(email=body.email.lower(), password=hasher.hash(body.password))
    session.add(user); session.commit()
    return {'message':'Account created. Sign in to continue.'}

@app.post('/api/auth/login')
def login(body: Credentials, session=Depends(db)):
    user = session.scalar(select(User).where(User.email == body.email.lower()))
    try:
        if not user or not hasher.verify(user.password, body.password): raise ValueError()
    except Exception: raise HTTPException(401, 'Invalid credentials')
    token = jwt.encode({'sub':user.id,'exp':datetime.now(timezone.utc)+timedelta(hours=8)}, SECRET, algorithm='HS256')
    return {'access_token':token,'role':user.role,'organization_id':user.organization_id}

@app.get('/api/me')
def me(user=Depends(actor)): return {'email':user.email,'role':user.role,'organization_id':user.organization_id}

@app.get('/api/organizations')
def organizations(session=Depends(db)):
    return [{'id':o.id,'name':o.name,'district':o.district} for o in session.scalars(select(Organization))]

def distance(a,b,c,d):
    p1,p2 = radians(a),radians(c); dl = radians(d-b); dp = p2-p1
    return 12742*asin(sqrt(sin(dp/2)**2+cos(p1)*cos(p2)*sin(dl/2)**2))

@app.get('/api/duplicates')
def duplicates(category: str, latitude: float, longitude: float, session=Depends(db)):
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180: raise HTTPException(422, 'Invalid coordinates')
    rows = session.scalars(select(Report).where(Report.category == category, Report.status.notin_(['Closed']), Report.created_at >= datetime.now(timezone.utc)-timedelta(days=90)).limit(500)).all()
    return [public(r,session) for r in rows if distance(latitude,longitude,r.latitude,r.longitude)<0.25][:5]

def read_image(raw):
    if len(raw)>6_000_000: raise HTTPException(413, 'Image exceeds 6 MB')
    try:
        image=Image.open(io.BytesIO(raw)); image.verify()
        image=Image.open(io.BytesIO(raw)); image.thumbnail((1600,1600))
        out=io.BytesIO(); image.convert('RGB').save(out, format='JPEG',quality=78,optimize=True)
        return out.getvalue()
    except Exception: raise HTTPException(422, 'Invalid image')

@app.post('/api/reports')
async def create_report(category: str=Form(...), description: str=Form(...), community: str=Form(...), latitude: float=Form(...), longitude: float=Form(...), photo: UploadFile=File(...), user=Depends(actor), session=Depends(db)):
    if category not in CATEGORIES or not 5<=len(description.strip())<=1000 or not 2<=len(community.strip())<=120 or not -90<=latitude<=90 or not -180<=longitude<=180: raise HTTPException(422, 'Invalid report fields')
    raw=await photo.read(6_000_001); image=read_image(raw)
    report=Report(tracking='FMA-'+str(datetime.now(timezone.utc).year)+'-'+uuid.uuid4().hex[:8].upper(),reporter_id=user.id,category=category,description=description.strip(),community=community.strip(),latitude=latitude,longitude=longitude)
    session.add(report); session.flush(); session.add(Photo(report_id=report.id,kind='before',data=image)); session.add(Event(report_id=report.id,actor_id=user.id,action='Submitted')); session.add(Notice(user_id=user.id,message=f'{report.tracking} submitted'))
    session.commit(); return public(report,session)

@app.get('/api/reports')
def reports(category: str|None=None,status: str|None=None,session=Depends(db)):
    query=select(Report).order_by(Report.created_at.desc()).limit(200)
    if category: query=query.where(Report.category==category)
    if status: query=query.where(Report.status==status)
    return [public(r,session) for r in session.scalars(query)]

@app.get('/api/reports/mine')
def mine(user=Depends(actor),session=Depends(db)):
    return [public(r,session) for r in session.scalars(select(Report).where(Report.reporter_id==user.id).order_by(Report.created_at.desc()))]

@app.get('/api/reports/{identifier}')
def report_detail(identifier: str,session=Depends(db)):
    r=session.scalar(select(Report).where((Report.id==identifier)|(Report.tracking==identifier)))
    if not r: raise HTTPException(404, 'Report not found')
    result=public(r,session); result['timeline']=[{'action':e.action,'note':e.note,'at':e.at.isoformat()} for e in session.scalars(select(Event).where(Event.report_id==r.id).order_by(Event.at))]
    return result

@app.get('/api/images/{photo_id}')
def image(photo_id: str,session=Depends(db)):
    p=session.get(Photo,photo_id)
    if not p: raise HTTPException(404, 'Image not found')
    return Response(content=p.data,media_type='image/jpeg',headers={'Cache-Control':'public, max-age=3600'})

@app.post('/api/reports/{report_id}/support')
def support(report_id: str,user=Depends(actor),session=Depends(db)):
    if not session.get(Report,report_id): raise HTTPException(404, 'Report not found')
    if not session.get(Support,{'report_id':report_id,'user_id':user.id}): session.add(Support(report_id=report_id,user_id=user.id)); session.commit()
    return {'supported':True}

class Assignment(BaseModel):
    organization_id: str
    assignee_id: str|None=None

@app.post('/api/reports/{report_id}/assign')
def assign(report_id: str,body: Assignment,user=Depends(actor),session=Depends(db)):
    staff(user); r=session.get(Report,report_id)
    if not r: raise HTTPException(404,'Report not found')
    if user.role!='platform_admin' and body.organization_id!=user.organization_id: raise HTTPException(403,'Outside your organization')
    if r.organization_id and user.role!='platform_admin' and r.organization_id!=user.organization_id: raise HTTPException(403,'Outside your organization')
    if not session.get(Organization,body.organization_id): raise HTTPException(404,'Organization not found')
    if body.assignee_id:
        assignee=session.get(User,body.assignee_id)
        if not assignee or assignee.organization_id!=body.organization_id or assignee.role=='citizen': raise HTTPException(422,'Invalid assignee')
    r.organization_id=body.organization_id; r.assignee_id=body.assignee_id; r.status='Assigned'
    session.add(Event(report_id=r.id,actor_id=user.id,action='Assigned')); session.add(Notice(user_id=r.reporter_id,message=f'{r.tracking} assigned')); session.commit(); return public(r,session)

class StatusUpdate(BaseModel):
    status: str
    note: str=Field(default='',max_length=1000)

@app.post('/api/reports/{report_id}/status')
def update_status(report_id: str,body: StatusUpdate,user=Depends(actor),session=Depends(db)):
    r=session.get(Report,report_id)
    if not r: raise HTTPException(404,'Report not found')
    accessible(r,user)
    allowed={'Assigned':['In Progress'],'In Progress':['Resolved'],'Resolved':['Closed','In Progress'],'Submitted':['Under Review'],'Under Review':['Verified'],'Verified':['Assigned']}
    if body.status not in allowed.get(r.status,[]): raise HTTPException(409,'Invalid status transition')
    if body.status=='Resolved' and not session.scalar(select(Photo).where(Photo.report_id==r.id,Photo.kind=='after')): raise HTTPException(409,'Upload after evidence first')
    r.status=body.status; session.add(Event(report_id=r.id,actor_id=user.id,action=body.status,note=body.note)); session.add(Notice(user_id=r.reporter_id,message=f'{r.tracking}: {body.status}')); session.commit(); return public(r,session)

@app.post('/api/reports/{report_id}/resolution')
async def resolution(report_id: str,photo: UploadFile=File(...),note: str=Form(''),user=Depends(actor),session=Depends(db)):
    r=session.get(Report,report_id)
    if not r: raise HTTPException(404,'Report not found')
    accessible(r,user)
    if r.status!='In Progress': raise HTTPException(409,'Report must be in progress')
    image=read_image(await photo.read(6_000_001)); session.add(Photo(report_id=r.id,kind='after',data=image)); session.add(Event(report_id=r.id,actor_id=user.id,action='After evidence uploaded',note=note[:1000])); session.commit(); return {'uploaded':True}

@app.get('/api/notifications')
def notifications(user=Depends(actor),session=Depends(db)):
    return [{'message':n.message,'at':n.at.isoformat()} for n in session.scalars(select(Notice).where(Notice.user_id==user.id).order_by(Notice.at.desc()).limit(50))]

@app.get('/api/analytics')
def analytics(session=Depends(db)):
    rows=session.scalars(select(Report)).all(); orgs=session.scalar(select(func.count()).select_from(Organization)) or 0
    return {'reported':len(rows),'resolved':sum(r.status in ('Resolved','Closed') for r in rows),'communities':len({r.community.lower() for r in rows}),'organizations':orgs,'by_status':{s:sum(r.status==s for r in rows) for s in STATES}}

@app.get('/api/organization/reports')
def organization_reports(user=Depends(actor),session=Depends(db)):
    staff(user)
    query=select(Report).where(Report.organization_id==user.organization_id) if user.role!='platform_admin' else select(Report)
    return [public(r,session) for r in session.scalars(query.order_by(Report.created_at.desc()).limit(200))]
