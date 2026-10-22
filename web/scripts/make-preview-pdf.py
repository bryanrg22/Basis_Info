"""Generate an explicitly fictional PDF asset for the local design preview."""
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color

base = Path(__file__).resolve().parents[1]
output = base / 'output/pdf/harbor-house-sample.pdf'
c = canvas.Canvas(str(output), pagesize=(612, 792), invariant=1)
c.setTitle('Harbor House - illustrative appraisal')
c.setAuthor('Basis design preview')
c.setSubject('Fictional design sample. Not a professional appraisal.')
ink = HexColor('#283f32'); muted = HexColor('#52685a'); line = HexColor('#d3ddd5')
sections = [
 ('Property summary', [('Property name', 'Harbor House'), ('Property address', '1200 Harbor Boulevard'), ('City and state', 'Ventura, California'), ('Property type', 'Single-family residential'), ('Client', 'Harbor Property Partners'), ('Effective date', 'September 30, 2026')], 'The following pages contain fictional property details for demonstrating document review in Basis.'),
 ('Property context', [('Use', 'Residential'), ('Occupancy', 'Owner occupied'), ('Stories', 'Two'), ('Architectural style', 'Contemporary'), ('Neighborhood', 'Illustrative residential neighborhood')], 'This document does not describe a verified property or a commissioned valuation.'),
 ('Building details', [('Year built', '1998'), ('Gross living area', '3,240 sq ft'), ('Bedrooms', '4'), ('Bathrooms', '3'), ('Construction', 'Wood frame'), ('Condition', 'Illustrative average condition')], 'Building details are sample values used to demonstrate the appraisal review interface.'),
 ('Interior description', [('Living spaces', 'Living room, kitchen and dining area'), ('Kitchen', 'Built-in cabinetry and work surfaces'), ('Flooring', 'Mixed carpet and hard surfaces'), ('Lighting', 'Recessed and decorative fixtures')], 'Room and component details are illustrative. Quantities and classifications require separate engineer review.'),
 ('Condition notes', [('Inspection', 'No inspection performed'), ('Photographs', 'Demonstration imagery only'), ('Measurements', 'Illustrative values'), ('Verification', 'Not independently verified')], 'No condition, compliance or engineering conclusion can be drawn from this sample.'),
 ('Valuation context', [('Document purpose', 'Interface design demonstration'), ('Data source', 'Fictional local fixtures'), ('Approach', 'No valuation analysis performed'), ('Reliance', 'Not for financial or tax use')], 'The values in this file are interface fixtures, not valuation opinions or tax advice.'),
 ('Site value', [('Property address', '1200 Harbor Boulevard'), ('Site value', '$280,000'), ('Value type', 'Illustrative land value'), ('Source', 'Design fixture'), ('Currency', 'USD')], 'The site value is included solely to demonstrate locating an extracted field in a PDF.'),
 ('Value summary', [('Property name', 'Harbor House'), ('Appraised value', '$1,250,000'), ('Site value', '$280,000'), ('Effective date', 'September 30, 2026'), ('Document status', 'Fictional design sample')], 'This is not an appraisal. A production study must use its original source document and supporting evidence.'),
]
for number, (title, rows, note) in enumerate(sections, 1):
 c.setFillColor(ink); c.setFont('Helvetica-Bold', 21); c.drawString(46, 743, 'basis')
 c.setFillColor(muted); c.setFont('Helvetica', 9); c.drawRightString(566, 748, 'Illustrative property appraisal')
 c.setStrokeColor(line); c.line(46, 724, 566, 724)
 c.setFillColor(muted); c.setFont('Helvetica', 10); c.drawString(46, 693, 'Harbor House / Appraisal source preview')
 c.setFillColor(ink); c.setFont('Helvetica-Bold', 27); c.drawString(46, 653, title)
 c.setFillColor(HexColor('#eff4ef')); c.roundRect(46, 578, 520, 48, 7, fill=1, stroke=0)
 c.setFillColor(muted); c.setFont('Helvetica-Bold', 9); c.drawString(61, 605, 'DESIGN SAMPLE')
 c.setFont('Helvetica', 9); c.drawString(61, 590, 'Fictional data. Not a professional appraisal.')
 y = 542
 for label, value in rows:
  c.setFillColor(muted); c.setFont('Helvetica', 10); c.drawString(46, y, label)
  c.setFillColor(ink); c.setFont('Helvetica-Bold', 11); c.drawString(232, y, value)
  c.setStrokeColor(line); c.line(46, y-17, 566, y-17); y -= 49
 c.setFillColor(muted); c.setFont('Helvetica', 10)
 words = note.split(); lines=[]; current=''
 for word in words:
  trial=(current+' '+word).strip()
  if c.stringWidth(trial, 'Helvetica', 10)>500: lines.append(current); current=word
  else: current=trial
 if current: lines.append(current)
 for text in lines: c.drawString(46, y-19, text); y-=16
 c.setStrokeColor(line); c.line(46, 64, 566, 64)
 c.setFillColor(muted); c.setFont('Helvetica', 8)
 c.drawString(46, 47, 'Basis design preview - not for financial or tax use')
 c.drawRightString(566, 47, f'Page {number} of {len(sections)}')
 c.showPage()
c.save()
print(output)
