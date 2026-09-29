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
    def login(email):
        response=client.post('/api/auth/login',json={'email':email,'password':'correct-horse-battery'})
        assert response.status_code==200,response.text
        return {'Authorization':'Bearer '+response.json()['access_token']}
    assert client.post('/api/auth/register',json={'email':'citizen@example.org','password':'correct-horse-battery'}).status_code==200
    citizen=login('citizen@example.org'); officer=login('officer@example.org'); outsider=login('other@example.org')
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
    print('Citizen to resolution workflow passed')
