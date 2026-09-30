"""Run with python test_flow.py using an isolated local database."""
import io
import os
import tempfile
from PIL import Image

with tempfile.TemporaryDirectory() as folder:
    os.environ['DATABASE_URL'] = 'sqlite:///' + folder + '/test.db'
    os.environ['JWT_SECRET'] = 'test-only-secret'
    from fastapi.testclient import TestClient
    from app import app, Session, Organization, User, hasher
    client = TestClient(app)
    with Session.begin() as session:
        org = Organization(name='Test Assembly', district='Test District')
        other = Organization(name='Other Assembly', district='Other District')
        session.add_all([org, other]); session.flush()
        org_id, other_id = org.id, other.id
        session.add_all([User(email='officer@example.org', password=hasher.hash('correct-horse-battery'), role='org_admin', organization_id=org_id),User(email='other@example.org',password=hasher.hash('correct-horse-battery'),role='org_admin',organization_id=other_id)])
        session.add(User(email='platform@example.org',password=hasher.hash('correct-horse-battery'),role='platform_admin'))
    def login(email):
        response=client.post('/api/auth/login',json={'email':email,'password':'correct-horse-battery'})
        assert response.status_code==200,response.text
        return {'Authorization':'Bearer '+response.json()['access_token']}
    assert client.post('/api/auth/register',json={'email':'citizen@example.org','password':'correct-horse-battery'}).status_code==200
    citizen=login('citizen@example.org'); officer=login('officer@example.org'); outsider=login('other@example.org')
    platform=login('platform@example.org')
    assert client.post(f'/api/organizations/{org_id}/verification',headers=officer,json={'verified':True}).status_code==403
    assert client.post('/api/organization/routing-rules',headers=officer,json={'category':'Road/Pothole','community':'Test Market','target_days':2}).status_code==200
    assert client.post(f'/api/organizations/{org_id}/verification',headers=platform,json={'verified':True}).status_code==200
    image=Image.new('RGB',(32,32),'red'); out=io.BytesIO(); image.save(out,format='PNG')
    response=client.post('/api/reports',headers=citizen,data={'category':'Road/Pothole','description':'Deep pothole near the market','community':'Test Market','latitude':'5.6','longitude':'-0.2'},files={'photo':('issue.png',out.getvalue(),'image/png')})
    assert response.status_code==200,response.text
    report=response.json(); rid=report['id']; assert report['tracking'].startswith('FMA-')
    assert client.get('/api/duplicates?category=Road/Pothole&latitude=5.6&longitude=-0.2').json()[0]['id']==rid
    assert client.post(f'/api/reports/{rid}/support',headers=citizen).status_code==200
    assert client.post(f'/api/reports/{rid}/status',headers=citizen,json={'status':'Under Review'}).status_code==403
    assert client.post(f'/api/reports/{rid}/assign',headers=officer,json={'organization_id':org_id}).status_code==200
    assert client.post(f'/api/reports/{rid}/status',headers=outsider,json={'status':'In Progress'}).status_code==403
    assert client.get('/api/organization/reports',headers=outsider).json()==[]
    assert client.post(f'/api/reports/{rid}/status',headers=officer,json={'status':'In Progress'}).status_code==200
    assert client.post(f'/api/reports/{rid}/status',headers=officer,json={'status':'Resolved'}).status_code==409
    assert client.post(f'/api/reports/{rid}/resolution',headers=officer,files={'photo':('fixed.png',out.getvalue(),'image/png')}).status_code==200
    assert client.post(f'/api/reports/{rid}/status',headers=officer,json={'status':'Resolved','note':'Road repaired'}).status_code==200
    assert client.get('/api/analytics').json()['resolved']==1
    assert len(client.get('/api/reports/'+rid).json()['timeline'])>=4
    import uuid
    assert client.get('/api/organization/review-queue',headers=outsider).json()==[]
    assert client.get('/api/organization/analytics',headers=outsider).json()['total']==0
    visit={'id':str(uuid.uuid4()),'report_id':rid,'note':'Internal inspection note'}
    assert client.post('/api/field/visits',headers=outsider,json=visit).status_code==403
    assert client.post('/api/field/visits',headers=officer,json=visit).json()['duplicate']==False
    assert client.post('/api/field/visits',headers=officer,json=visit).json()['duplicate']==True
    assert 'Internal inspection note' not in client.get('/api/reports/'+rid).text
    assert client.get('/api/reports/'+rid+'/field-visits',headers=citizen).status_code==403
    assert client.post('/api/reports/'+rid+'/confirmation',headers=outsider,json={'resolved':True}).status_code==403
    assert client.post('/api/reports/'+rid+'/confirmation',headers=citizen,json={'resolved':False,'note':'Still damaged near the edge'}).json()['status']=='Reopened'
    assert client.post('/api/reports/'+rid+'/status',headers=officer,json={'status':'In Progress'}).status_code==200
    assert client.post('/api/reports/'+rid+'/status',headers=officer,json={'status':'Resolved'}).status_code==200
    assert client.post('/api/reports/'+rid+'/confirmation',headers=citizen,json={'resolved':True}).json()['status']=='Closed'
    assert client.post('/api/reports/'+rid+'/confirmation',headers=citizen,json={'resolved':True}).status_code==409
    assert client.post('/api/partnerships/interest',headers=citizen,json={'kind':'Contributor','message':'I would like to contribute GIS expertise'}).status_code==200
    assert client.get('/api/partnerships/interest',headers=officer).status_code==403
    assert len(client.get('/api/partnerships/interest',headers=platform).json())==1
    assert client.get('/api/organization/analytics',headers=officer).json()['resolution_rate']==100
    from app import Report
    from datetime import datetime,timedelta,timezone
    assert client.get('/api/analytics/hotspots').json()==[]
    with Session.begin() as session:
        reporter=session.query(User).filter_by(email='citizen@example.org').one()
        for i in range(3): session.add(Report(tracking='TEST-'+str(i),reporter_id=reporter.id,organization_id=org_id,category='Drainage',community='Old Market',description='Test fixture only',latitude=5.6,longitude=-0.2,created_at=datetime.now(timezone.utc)-timedelta(days=30)))
    assert client.get('/api/analytics/hotspots').json()[0]['count']==3
    assert client.get('/api/organization/analytics',headers=officer).json()['overdue']==3
    print('Citizen to resolution workflow passed')
