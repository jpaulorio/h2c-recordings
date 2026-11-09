#!/usr/bin/env python3
"""Optional API smoke test; requires the synthetic fixture, never a real printer."""
import json
import sys
import time
import urllib.error
import urllib.request
BASE=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:3211'
def call(path,method='GET',data=None,headers=None):
    body=json.dumps(data).encode() if data is not None else None
    request=urllib.request.Request(BASE+'/api'+path,data=body,method=method,headers={'Content-Type':'application/json',**(headers or {})})
    try:
        with urllib.request.urlopen(request,timeout=30) as response:return response.status,response.read(),response.headers
    except urllib.error.HTTPError as error:return error.code,error.read(),error.headers
settings=json.loads(call('/settings')[1]);assert settings['host']=='127.0.0.1' and settings['port']==1990,'Run only against the test fixture on port 1990'
assert 'accessCode' not in settings
assert call('/queue','POST',{},headers={'Origin':'https://example.invalid'})[0]==403
assert call('/queue','POST',{},headers={'Content-Type':'text/plain'})[0]==415
jobs=json.loads(call('/jobs','POST',{})[1]);assert len(jobs)==2 and all(job['count']==2 for job in jobs)
items=json.loads(call('/queue','POST',{'ids':[job['id'] for job in jobs]})[1]);assert len(items)==2
assert call('/settings','PUT',{})[0]==409
assert json.loads(call('/queue','POST',{'ids':[jobs[0]['id']]})[1])==[]
assert call('/queue/'+items[0]['id']+'/cancel','POST',{})[0]==200
end=time.monotonic()+60
while time.monotonic()<end:
    queue=json.loads(call('/queue')[1]);chosen={item['id']:item for item in queue if item['id'] in [x['id'] for x in items]}
    if all(item['status'] not in ('queued','downloading','merging') for item in chosen.values()):break
    time.sleep(.2)
assert chosen[items[0]['id']]['status']=='cancelled',chosen
assert chosen[items[1]['id']]['status']=='complete',chosen
assert chosen[items[1]['id']]['progress']['percent']==100
videos=json.loads(call('/library')[1]);assert len(videos)==2
status,body,headers=call('/videos/'+videos[0]['id'],headers={'Range':'bytes=0-15'})
assert status==206 and len(body)==16 and headers['Content-Range'].startswith('bytes 0-15/')
assert call('/videos/'+videos[0]['id'],headers={'Range':'bytes=999999999-'})[0]==416
print('PASS: filtering, multi-job queue, cancellation, continued download, merging, progress, library, ranged playback, settings lock and origin checks.')
