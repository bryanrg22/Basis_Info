"""Read-only synthetic API for navigation and PDF source browser QA.
Run: python3 web/tests/fixtures/navigation-source-server.py
Use the web preview on port 3100 with ?mode=live&api=http://127.0.0.1:8016.
Requires the existing local auth emulator session; does not validate credentials,
write study data, or implement workflow mutations. Binds loopback only.
"""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse
import json
def pdf(pages):
 objs=['<< /Type /Catalog /Pages 2 0 R >>','', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];kids=[]
 for n,(rotation,lines) in enumerate(pages):
  pageid=len(objs)+1;kids.append(f'{pageid} 0 R');cid=pageid+1
  objs.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Rotate {rotation} /Resources << /Font << /F1 3 0 R >> >> /Contents {cid} 0 R >>')
  commands=['0.13 0.23 0.18 rg']
  for x,y,size,text in lines:
   text=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)');commands.append(f'BT /F1 {size} Tf {x} {y} Td ({text}) Tj ET')
  data='\n'.join(commands);objs.append(f'<< /Length {len(data)} >>\nstream\n{data}\nendstream')
 objs[1]=f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {len(pages)} >>';out='%PDF-1.4\n';offsets=[]
 for i,obj in enumerate(objs):offsets.append(len(out));out+=f'{i+1} 0 obj\n{obj}\nendobj\n'
 xref=len(out);out+=f'xref\n0 {len(objs)+1}\n0000000000 65535 f \n'+''.join(f'{v:010} 00000 n \n' for v in offsets)+f'trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n';return out.encode()
pages=[(0,[(42,744,10,'SYNTHETIC QA APPRAISAL - NOT A REAL PROPERTY'),(42,690,23,'Property details'),(42,600,12,'Property address'),(42,570,18,'1200 Harbor Boulevard'),(42,280,12,'Year built'),(42,250,18,'1998')]),(0,[(42,744,10,'SYNTHETIC QA APPRAISAL - PAGE 2'),(42,690,23,'Land and improvements'),(42,240,12,'Site value'),(42,210,20,'$280,000'),(42,160,12,'Effective date'),(42,130,16,'September 30, 2026')]),(0,[(42,744,10,'SYNTHETIC QA APPRAISAL - PAGE 3'),(42,690,23,'Cost approach'),(42,400,12,'Basement cost per square foot'),(42,375,20,'90.90'),(42,235,12,'Alternate estimate'),(42,210,20,'90.90')]),(90,[(42,744,10,'SYNTHETIC QA - ROTATED SOURCE PAGE'),(42,500,22,'Gross living area'),(42,460,20,'3,240')]),(0,[])]
PDF=pdf(pages)
fields=[('address','Property address','1200 Harbor Boulevard',None,None,1),('year_built','Year built',None,1998,None,1),('site_value','Site value',None,280000,None,2),('effective_date','Effective date',None,None,'2026-09-30',2),('basement_unit_cost','Basement cost per square foot',None,90.90,None,3),('gross_living_area','Gross living area',None,3240,None,4),('missing_value','Unmatched value','Not printed on this page',None,None,2),('unknown_source','No page reference','Unreferenced value',None,None,None),('bad_reference','Invalid page reference','Out of range',None,None,99),('image_only','Image-only page','Scanned value',None,None,5)]
FIELDS=[dict(id=f,extraction_id='extraction',document_id='appraisal',section='property',field=f,key='property.'+f,label=l,value_text=t,value_num=n,value_date=d,page=p,confidence=.95,critical=False,source='manual') for f,l,t,n,d,p in fields]
class Handler(BaseHTTPRequestHandler):
 def send(self,obj,typ='application/json',status=200):
  b=json.dumps(obj).encode() if typ=='application/json' else obj
  self.send_response(status);self.send_header('Access-Control-Allow-Origin','http://127.0.0.1:3100');self.send_header('Access-Control-Allow-Headers','Authorization,Content-Type,Idempotency-Key');self.send_header('Access-Control-Allow-Methods','GET,OPTIONS');self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_OPTIONS(self):self.send({})
 def do_GET(self):
  p=urlparse(self.path).path
  if p=='/v1/me':return self.send(dict(user=dict(id='qa',email='gates-engineer@example.test',display_name='UI QA'),email_verified=True,memberships=[dict(org_id='qa',org_name='Synthetic UI QA',role='engineer')],pending_invitations=[]))
  parts=p.split('/');sid=parts[3] if len(parts)>3 else 'six';gate=sid!='legacy'
  stage='completed' if sid=='finished' else 'components_processing' if sid=='preparing' else 'uploading' if sid=='draft' else 'appraisal_review' if sid=='editable' else 'assets_review';status='completed' if sid=='finished' else 'processing' if sid=='preparing' else 'draft' if sid=='draft' else 'waiting_review'
  if p.endswith('/fields'):return self.send(dict(revision=1,items=FIELDS,next_cursor=None))
  if p.endswith('/rooms'):return self.send(dict(revision=1,items=[dict(id='kitchen',name='Kitchen',room_type='kitchen',floor='1',source='manual',needs_review=False,photos=[],inventory=[],suggested_counts=[]),dict(id='living',name='Living room',room_type='living_room',floor='1',source='manual',needs_review=False,photos=[],inventory=[],suggested_counts=[])],next_cursor=None))
  if p.endswith('/assets'):return self.send(dict(revision=1,items=[],next_cursor=None))
  if p.endswith('/workflow'):return self.send(dict(execution_status='RUNNING',status=None))
  if p.endswith('/content'):return self.send(PDF,'application/pdf')
  if p.startswith('/v1/studies/') and len(parts)==4:return self.send(dict(id=sid,org_id='qa',created_by='qa',property_name='Source navigation QA',status=status,current_stage=stage,components_gate=gate,revision=1,updated_at='2026-10-24T12:00:00Z',documents=[dict(id='appraisal',kind='appraisal',filename='Synthetic_Appraisal.pdf',content_type='application/pdf',bytes=len(PDF),uploaded_at='2026-10-24T10:00:00Z')]))
  return self.send({'detail':'Unimplemented fixture path: '+p},status=404)
if __name__=='__main__':
 print('Synthetic read-only API: http://127.0.0.1:8016',flush=True)
 ThreadingHTTPServer(('127.0.0.1',8016),Handler).serve_forever()
