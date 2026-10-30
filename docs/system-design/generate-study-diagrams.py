"""Render the October 27 code snapshot as two plain SVG study-flow diagrams."""
from pathlib import Path
from html import escape
P = Path(__file__).parent
class Diagram:
    def __init__(self,w,h,title,subtitle):
        self.s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img"><title>{escape(title)}</title><defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10" fill="#2563eb"/></marker></defs><rect width="{w}" height="{h}" fill="white"/><style>text{{font-family:Arial,Helvetica,sans-serif;fill:#152332}} .secondary{{fill:#566779}} .accent{{fill:#2563eb}}</style>']
        self.text(45,52,title,29)
        self.text(45,84,subtitle,17,'secondary')
    def text(self,x,y,s,size=18,cls='',anchor='start'):
        self.s.append(f'<text x="{x}" y="{y}" font-size="{size}" class="{cls}" text-anchor="{anchor}">{escape(s)}</text>')
    def box(self,x,y,w,h,title,lines=(),review=False):
        self.s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{ "#eff5ff" if review else "white"}" stroke="{ "#2563eb" if review else "#243242"}" stroke-width="2"/>')
        self.text(x+w/2,y+34,title,21,'accent' if review else '', 'middle')
        for i,l in enumerate(lines):self.text(x+w/2,y+62+i*23,l,17,'secondary','middle')
    def arrow(self,d,label='',x=0,y=0,dash=False):
        self.s.append(f'<path d="{d}" fill="none" stroke="#2563eb" stroke-width="2.5" marker-end="url(#a)"'+(' stroke-dasharray="6 5"' if dash else '')+'/>')
        if label:self.text(x,y,label,16,'accent','middle')
    def save(self,name): (P/(name+'.svg')).write_text('\n'.join(self.s+['</svg>']))

v=Diagram(1450,520,'Basis — the simple view','Current backend workflow in main · October 27, 2026 · fcf60ac · code view, not deployment verification')
v.box(60,190,310,130,'Upload study files',('Appraisal report (PDF)','Property photos — e.g. 5 photos'))
v.box(530,175,385,160,'Study workflow',('Extract appraisal + analyze photos','Engineer reviews at each checkpoint','Build assets, quantities and costs'))
v.box(1075,190,310,130,'Study output files',('Assets CSV','JSON study + evidence pack'))
v.arrow('M370 255 H530')
v.arrow('M915 255 H1075','final approval',995,237)
v.text(725,390,'The appraisal and photo paths start together. Photo analysis does not wait for appraisal approval.',20,anchor='middle')
v.text(725,431,'Temporal coordinates the workflow; PostgreSQL stores results; object storage holds inputs and exports.',17,'secondary','middle')
v.text(725,476,'Expanded in the second diagram ↓',18,'accent','middle')
v.save('study-overview-2026-10-27')

v=Diagram(1600,1300,'Basis — inside the study workflow','Default path for a new study in current main · blue boxes are engineer approval checkpoints')
v.box(585,118,430,100,'1. Upload + start study',('Appraisal PDF + all property photos',))
v.arrow('M695 218 V242 H310 V270','appraisal report',443,233)
v.arrow('M905 218 V242 H1280 V270','photos — e.g. 5',1146,233)
v.text(800,253,'RUN IN PARALLEL',17,'accent','middle')
v.box(95,270,430,122,'2A. Appraisal extraction engine',('Read PDF text + tables; Azure DI if configured','Merge, validate, fill missing fields with AI','Result: structured appraisal fields'))
v.box(1065,270,430,122,'2B. Analyze each photo',('Vision AI: room identity, objects, surfaces','Initial component counts + materials per photo','Optional SAM 3 object outlines in background'))
v.arrow('M310 392 V430')
v.arrow('M1280 392 V430')
v.box(95,430,430,102,'3. Engineer reviews appraisal',('Check extracted values; approve or correct',),True)
v.box(1065,430,430,102,'Photo results + proposed rooms',('Group the photos by room; initial components',))
v.arrow('M310 532 V564 H650 V600','appraisal approved',451,554)
v.arrow('M1280 532 V564 H950 V600','photo analysis ready',1140,554)
v.box(585,600,430,102,'4. Engineer reviews room grouping',('Which photos belong to which room?',),True)
v.text(800,743,'Room grouping approved → build the room-level inventory',20,'accent','middle')
v.arrow('M800 702 V720 H240 V790')
v.box(60,790,350,145,'5. Build component inventory',('Reconcile photos of each room','Reduce duplicate counts across photos','Check materials; await object outlines'))
v.box(445,790,350,145,'6. Engineer reviews components',('Verify objects, counts and materials','Inspect photo evidence / outlines','Approve the room-level inventory'),True)
v.box(830,790,330,145,'7. Process approved inventory',('IRS asset classification + evidence','Estimate quantities / takeoffs','Estimate costs; flag uncertainties'))
v.box(1195,790,345,145,'8. Engineer reviews assets',('Check classification, quantities, costs','Review citations and flagged items','Approve the final study revision'),True)
v.arrow('M410 862 H445')
v.arrow('M795 862 H830')
v.arrow('M1160 862 H1195')
v.arrow('M1367 935 V994','approved',1420,971)
v.box(1150,994,390,145,'9. Generate output files',('Read the approved revision from the database','Assets CSV + JSON study / evidence pack','Save files to object storage'))
v.text(60,1030,'When are components extracted?',21)
v.text(60,1065,'First during per-photo analysis (2B), before engineer approval.',19)
v.text(60,1095,'Then consolidated into a room inventory (5), after room grouping approval.',19)
v.text(60,1154,'If the engineer rejects a checkpoint: save corrections → create a new revision → reprocess the relevant step → review again.',18,'accent')
v.text(60,1200,'Five photos means five per-photo analyses, not five rooms. Multiple views of one room are reconciled together.',18,'secondary')
v.text(60,1231,'Current limits: optional outlines depend on configuration; takeoffs can use typical room areas; costs depend on the configured table.',16,'secondary')
v.text(60,1262,'Backend code inspected at fcf60ac. Existing runs may use older workflow versions. These diagrams do not establish frontend or deployment readiness.',15,'secondary')
v.save('study-detail-2026-10-27')
