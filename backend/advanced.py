"""Additive pilot workflows; new tables do not rewrite existing reports."""
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.exc import IntegrityError


def install(app, core):
    Base, Session = core['Base'], core['Session']
    Report, User, Organization = core['Report'], core['User'], core['Organization']
    Event, Notice = core['Event'], core['Notice']
    db, actor, staff, accessible, public = (core[k] for k in ['db','actor','staff','accessible','public'])
    now = lambda: datetime.now(timezone.utc)

    class OrganizationProfile(Base):
        __tablename__ = 'organization_profiles'
        organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), primary_key=True)
        verified: Mapped[bool] = mapped_column(Boolean, default=False)
        verified_by: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
        at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    class RoutingRule(Base):
        __tablename__ = 'routing_rules'
        id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
        category: Mapped[str] = mapped_column(String(50))
        community: Mapped[str] = mapped_column(String(120))
        target_days: Mapped[int] = mapped_column(Integer, default=14)
        __table_args__ = (UniqueConstraint('organization_id','category','community'),)

    class Confirmation(Base):
        __tablename__ = 'resolution_confirmations'
        id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        report_id: Mapped[str] = mapped_column(ForeignKey('reports.id'))
        user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
        resolved: Mapped[bool] = mapped_column(Boolean)
        note: Mapped[str] = mapped_column(Text)
        at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    class FieldVisit(Base):
        __tablename__ = 'field_visits'
        id: Mapped[str] = mapped_column(String(36), primary_key=True)
        report_id: Mapped[str] = mapped_column(ForeignKey('reports.id'))
        actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
        note: Mapped[str] = mapped_column(Text)
        at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    class Interest(Base):
        __tablename__ = 'partner_interests'
        id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
        kind: Mapped[str] = mapped_column(String(30))
        organization: Mapped[str] = mapped_column(String(150))
        message: Mapped[str] = mapped_column(Text)
        report_id: Mapped[str | None] = mapped_column(ForeignKey('reports.id'), nullable=True)
        at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    Base.metadata.create_all(core['engine'])

    def platform(user):
        if user.role != 'platform_admin': raise HTTPException(403, 'Platform administrator required')

    def suggestions(r, session):
        rules = session.scalars(select(RoutingRule).join(OrganizationProfile, RoutingRule.organization_id == OrganizationProfile.organization_id).where(OrganizationProfile.verified == True, RoutingRule.category == r.category, func.lower(RoutingRule.community) == r.community.lower())).all()
        return [{'organization_id':rule.organization_id,'name':session.get(Organization,rule.organization_id).name,'target_days':rule.target_days,'reason':'Category and configured community match'} for rule in rules]

    def due(r, session):
        rules=session.scalars(select(RoutingRule).where(RoutingRule.organization_id==r.organization_id, RoutingRule.category==r.category, func.lower(RoutingRule.community)==r.community.lower())).all()
        days=min((rule.target_days for rule in rules),default=14)
        created=r.created_at.replace(tzinfo=timezone.utc) if r.created_at.tzinfo is None else r.created_at
        deadline=created+timedelta(days=days)
        return deadline.isoformat(), r.status not in ('Resolved','Closed') and now()>deadline

    class VerifyBody(BaseModel):
        verified: bool

    @app.post('/api/organizations/{organization_id}/verification')
    def verify(organization_id: str, body: VerifyBody, user=Depends(actor), session=Depends(db)):
        platform(user)
        if not session.get(Organization,organization_id): raise HTTPException(404,'Organization not found')
        profile=session.get(OrganizationProfile,organization_id)
        if not profile: profile=OrganizationProfile(organization_id=organization_id); session.add(profile)
        profile.verified=body.verified; profile.verified_by=user.id; profile.at=now(); session.commit()
        return {'verified':profile.verified}

    class RuleBody(BaseModel):
        category: str
        community: str = Field(min_length=2,max_length=120)
        target_days: int = Field(default=14,ge=1,le=365)

    @app.post('/api/organization/routing-rules')
    def add_rule(body: RuleBody,user=Depends(actor),session=Depends(db)):
        staff(user)
        if user.role not in ('org_admin','supervisor') or not user.organization_id: raise HTTPException(403,'Organization administrator or supervisor required')
        if body.category not in core['CATEGORIES']: raise HTTPException(422,'Invalid category')
        community=body.community.strip()
        if len(community)<2: raise HTTPException(422,'Community is required')
        rule=session.scalar(select(RoutingRule).where(RoutingRule.organization_id==user.organization_id,RoutingRule.category==body.category,func.lower(RoutingRule.community)==community.lower()))
        if not rule: rule=RoutingRule(organization_id=user.organization_id,category=body.category,community=community); session.add(rule)
        rule.target_days=body.target_days; session.commit()
        return {'id':rule.id,'message':'Rule saved. Suggestions activate after platform verification.'}

    @app.get('/api/organization/routing-rules')
    def rules(user=Depends(actor),session=Depends(db)):
        staff(user)
        return [{'id':r.id,'category':r.category,'community':r.community,'target_days':r.target_days} for r in session.scalars(select(RoutingRule).where(RoutingRule.organization_id==user.organization_id))]

    core['routing_allowed'] = lambda report, organization_id, session: any(s['organization_id']==organization_id for s in suggestions(report,session))

    @app.get('/api/reports/{report_id}/routing')
    def routing(report_id: str,user=Depends(actor),session=Depends(db)):
        staff(user); r=session.get(Report,report_id)
        if not r: raise HTTPException(404,'Report not found')
        if r.organization_id: accessible(r,user)
        matches=suggestions(r,session)
        return {'suggestions':matches,'requires_review':len(matches)!=1}

    @app.get('/api/organization/review-queue')
    def review_queue(user=Depends(actor),session=Depends(db)):
        staff(user)
        rows=session.scalars(select(Report).where(Report.organization_id==None).order_by(Report.created_at).limit(200))
        # Organization staff only see unassigned cases matched to their verified service area.
        return [public(r,session) for r in rows if user.role=='platform_admin' or any(s['organization_id']==user.organization_id for s in suggestions(r,session))]

    class ConfirmBody(BaseModel):
        resolved: bool
        note: str = Field(default='',max_length=1000)

    @app.post('/api/reports/{report_id}/confirmation')
    def confirm(report_id: str,body: ConfirmBody,user=Depends(actor),session=Depends(db)):
        r=session.scalar(select(Report).where(Report.id==report_id).with_for_update())
        if not r: raise HTTPException(404,'Report not found')
        if r.reporter_id!=user.id: raise HTTPException(403,'Only the original reporter can confirm this repair')
        if r.status!='Resolved': raise HTTPException(409,'Report must be resolved before confirmation')
        if not body.resolved and len(body.note.strip())<5: raise HTTPException(422,'Describe why the issue needs reopening')
        session.add(Confirmation(report_id=r.id,user_id=user.id,resolved=body.resolved,note=body.note.strip()))
        r.status='Closed' if body.resolved else 'Reopened'
        session.add(Event(report_id=r.id,actor_id=user.id,action='Citizen confirmed repair' if body.resolved else 'Reopened',note=body.note.strip()))
        session.add(Notice(user_id=user.id,message=f'{r.tracking}: {r.status}'))
        recipients=session.scalars(select(User).where(User.organization_id==r.organization_id,User.role.in_(['officer','supervisor','org_admin']))) if r.organization_id else []
        for recipient in recipients: session.add(Notice(user_id=recipient.id,message=f'{r.tracking}: {r.status}'))
        session.commit(); return public(r,session)

    @app.get('/api/reports/{report_id}/confirmation')
    def confirmation_state(report_id: str,user=Depends(actor),session=Depends(db)):
        r=session.get(Report,report_id)
        if not r: raise HTTPException(404,'Report not found')
        return {'can_confirm':r.reporter_id==user.id and r.status=='Resolved'}

    class VisitBody(BaseModel):
        id: uuid.UUID
        report_id: uuid.UUID
        note: str = Field(min_length=5,max_length=1000)

    @app.post('/api/field/visits')
    def visit(body: VisitBody,user=Depends(actor),session=Depends(db)):
        r=session.scalar(select(Report).where(Report.id==str(body.report_id)).with_for_update())
        if not r: raise HTTPException(404,'Report not found')
        accessible(r,user)
        if user.role=='officer' and r.assignee_id!=user.id: raise HTTPException(403,'Task must be assigned to you')
        existing=session.get(FieldVisit,str(body.id))
        if existing:
            if existing.actor_id!=user.id or existing.report_id!=r.id: raise HTTPException(409,'Synchronization ID already used')
            if existing.note!=body.note.strip(): raise HTTPException(409,'Synchronization content differs')
            return {'saved':True,'duplicate':True}
        if len(body.note.strip())<5: raise HTTPException(422,'Inspection note is too short')
        session.add(FieldVisit(id=str(body.id),report_id=r.id,actor_id=user.id,note=body.note.strip()))
        # Inspection notes are internal. Public timelines contain only an event label.
        session.add(Event(report_id=r.id,actor_id=user.id,action='Field inspection recorded'))
        session.commit(); return {'saved':True,'duplicate':False}

    @app.get('/api/reports/{report_id}/field-visits')
    def visits(report_id: str,user=Depends(actor),session=Depends(db)):
        r=session.get(Report,report_id)
        if not r: raise HTTPException(404,'Report not found')
        accessible(r,user)
        return [{'id':v.id,'note':v.note,'at':v.at.isoformat()} for v in session.scalars(select(FieldVisit).where(FieldVisit.report_id==report_id).order_by(FieldVisit.at.desc()))]

    @app.get('/api/organization/analytics')
    def org_analytics(user=Depends(actor),session=Depends(db)):
        staff(user)
        query=select(Report)
        if user.role!='platform_admin': query=query.where(Report.organization_id==user.organization_id)
        rows=session.scalars(query).all()
        resolved=[r for r in rows if r.status in ('Resolved','Closed')]
        durations=[]
        for r in resolved:
            at=session.scalar(select(func.min(Event.at)).where(Event.report_id==r.id,Event.action=='Resolved'))
            if at: durations.append(max(0,(at-r.created_at).total_seconds()/86400))
        return {'total':len(rows),'resolved':len(resolved),'resolution_rate':round(100*len(resolved)/len(rows),1) if rows else 0,'average_resolution_days':round(sum(durations)/len(durations),1) if durations else None,'overdue':sum(due(r,session)[1] for r in rows),'by_status':dict(Counter(r.status for r in rows)),'targets':[{ 'id':r.id,'due_at':due(r,session)[0],'overdue':due(r,session)[1]} for r in rows]}

    @app.get('/api/analytics/hotspots')
    def hotspots(session=Depends(db)):
        # Coarse cells (~1 km latitude); aggregates never contain reporter identities.
        groups={}
        rows=session.scalars(select(Report).where(Report.status.notin_(['Resolved','Closed'])))
        for r in rows:
            key=(round(r.latitude,2),round(r.longitude,2),r.category)
            groups[key]=groups.get(key,0)+1
        return [{'latitude':k[0],'longitude':k[1],'category':k[2],'count':n} for k,n in sorted(groups.items(),key=lambda x:x[1],reverse=True) if n>=3][:50]

    class InterestBody(BaseModel):
        kind: str
        organization: str = Field(default='',max_length=150)
        message: str = Field(min_length=20,max_length=2000)
        report_id: uuid.UUID | None = None

    @app.post('/api/partnerships/interest')
    def interest(body: InterestBody,user=Depends(actor),session=Depends(db)):
        if body.kind not in ('Contributor','Pilot organization','NGO / CSR','Investment discussion'): raise HTTPException(422,'Invalid interest type')
        if len(body.message.strip())<20: raise HTTPException(422,'Add at least 20 characters')
        if body.report_id:
            r=session.scalar(select(Report).where(Report.id==str(body.report_id)).with_for_update())
            if not r or r.status not in ('Verified','Assigned','In Progress'): raise HTTPException(422,'Project must be verified and awaiting work')
        entry=Interest(user_id=user.id,kind=body.kind,organization=body.organization.strip(),message=body.message.strip(),report_id=str(body.report_id) if body.report_id else None)
        if (session.scalar(select(func.count()).select_from(Interest).where(Interest.user_id==user.id,Interest.at>=now()-timedelta(days=1))) or 0)>=5: raise HTTPException(429,'Please wait before sending another enquiry')
        session.add(entry);session.commit();return {'id':entry.id,'message':'Interest recorded for platform review. No funding commitment has been made.'}

    @app.get('/api/partnerships/interest')
    def interest_list(user=Depends(actor),session=Depends(db)):
        platform(user)
        return [{'id':i.id,'kind':i.kind,'organization':i.organization,'message':i.message,'email':session.get(User,i.user_id).email,'report_id':i.report_id,'at':i.at.isoformat()} for i in session.scalars(select(Interest).order_by(Interest.at.desc()).limit(200))]
