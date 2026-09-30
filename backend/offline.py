"""Private field evidence and repeat-safe organization attention alerts."""
import hashlib
import uuid
from datetime import datetime, timezone
from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column


def install(app, core):
    Base, User, Report, Event, Notice = (core[k] for k in ('Base','User','Report','Event','Notice'))
    actor, db, accessible, staff = (core[k] for k in ('actor','db','accessible','staff'))

    class Evidence(Base):
        __tablename__ = 'field_evidence'
        id: Mapped[str] = mapped_column(String(36), primary_key=True)
        actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
        report_id: Mapped[str] = mapped_column(ForeignKey('reports.id'))
        note: Mapped[str] = mapped_column(Text)
        digest: Mapped[str] = mapped_column(String(64))
        image: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
        at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    class Alert(Base):
        __tablename__ = 'overdue_alerts'
        user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
        report_id: Mapped[str] = mapped_column(ForeignKey('reports.id'), primary_key=True)
        deadline: Mapped[str] = mapped_column(String(50), primary_key=True)

    class Preference(Base):
        __tablename__ = 'notification_preferences'
        user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
        phone: Mapped[str] = mapped_column(String(20), default='')
        channel: Mapped[str] = mapped_column(String(20), default='in_app')

    Base.metadata.create_all(core['engine'])

    @app.post('/api/field/evidence')
    async def save_evidence(client_id: uuid.UUID=Form(...), report_id: uuid.UUID=Form(...), note: str=Form(...), photo: UploadFile | None=File(None), user=Depends(actor), session=Depends(db)):
        report=session.scalar(select(Report).where(Report.id==str(report_id)).with_for_update())
        if not report: raise HTTPException(404,'Report not found')
        accessible(report,user)
        if user.role=='officer' and report.assignee_id!=user.id: raise HTTPException(403,'Task must be assigned to you')
        note=note.strip()
        if not 5<=len(note)<=1000: raise HTTPException(422,'Inspection note must contain 5 to 1000 characters')
        raw=await photo.read(6_000_001) if photo else b''
        image=core['read_image'](raw) if photo else None
        digest=hashlib.sha256(note.encode()+raw).hexdigest()
        existing=session.get(Evidence,str(client_id))
        if existing:
            if existing.actor_id!=user.id or existing.report_id!=report.id or existing.digest!=digest: raise HTTPException(409,'Saved inspection ID has different content')
            return {'saved':True,'duplicate':True,'id':existing.id}
        entry=Evidence(id=str(client_id),actor_id=user.id,report_id=report.id,note=note,digest=digest,image=image)
        session.add(entry)
        session.add(Event(report_id=report.id,actor_id=user.id,action='Field evidence recorded'))
        session.commit()
        return {'saved':True,'duplicate':False,'id':entry.id}

    @app.get('/api/reports/{report_id}/field-evidence')
    def evidence_list(report_id: str,user=Depends(actor),session=Depends(db)):
        report=session.get(Report,report_id)
        if not report: raise HTTPException(404,'Report not found')
        accessible(report,user)
        return [{'id':e.id,'note':e.note,'has_photo':e.image is not None,'at':e.at.isoformat()} for e in session.scalars(select(Evidence).where(Evidence.report_id==report.id).order_by(Evidence.at.desc()))]

    @app.get('/api/field/evidence/{evidence_id}/photo')
    def evidence_photo(evidence_id: str,user=Depends(actor),session=Depends(db)):
        entry=session.get(Evidence,evidence_id)
        if not entry or entry.image is None: raise HTTPException(404,'Evidence photo not found')
        accessible(session.get(Report,entry.report_id),user)
        return Response(entry.image,media_type='image/jpeg',headers={'Cache-Control':'no-store'})

    @app.get('/api/organization/overdue')
    def overdue(user=Depends(actor),session=Depends(db)):
        staff(user)
        query=select(Report)
        if user.role!='platform_admin': query=query.where(Report.organization_id==user.organization_id)
        result=[]
        for r in session.scalars(query):
            deadline,late=core['report_due'](r,session)
            if late: result.append({**core['public'](r,session),'due_at':deadline})
        return sorted(result,key=lambda r:r['due_at'])

    @app.post('/api/organization/check-alerts')
    def check_alerts(user=Depends(actor),session=Depends(db)):
        staff(user)
        # Called on workspace access/refresh, not a background scheduler.
        session.scalar(select(User).where(User.id==user.id).with_for_update())
        rows=overdue(user,session)
        count=0
        for r in rows:
            key={'user_id':user.id,'report_id':r['id'],'deadline':r['due_at']}
            if not session.get(Alert,key):
                session.add(Alert(**key)); session.add(Notice(user_id=user.id,message=f"{r['tracking']}: overdue response target; review in Workspace")); count+=1
        session.commit()
        return {'created':count}

    class PreferenceBody(BaseModel):
        phone: str = Field(default='',max_length=20)
        channel: str

    @app.get('/api/notification-preferences')
    def preferences(user=Depends(actor),session=Depends(db)):
        pref=session.get(Preference,user.id)
        return {'phone':pref.phone if pref else '', 'channel':pref.channel if pref else 'in_app','sms_available':False,'whatsapp_available':False}

    @app.post('/api/notification-preferences')
    def save_preferences(body: PreferenceBody,user=Depends(actor),session=Depends(db)):
        # Do not collect telephone numbers or consent for unconfigured providers.
        if body.channel!='in_app' or body.phone: raise HTTPException(409,'SMS and WhatsApp providers are not connected yet. In-app updates remain available.')
        pref=session.get(Preference,user.id)
        if not pref: pref=Preference(user_id=user.id); session.add(pref)
        pref.channel='in_app'; pref.phone=''; session.commit()
        return {'saved':True}
