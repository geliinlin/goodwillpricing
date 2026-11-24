"""Read and decode the two PDF downloads made by the Linux release workflow."""
import json
from pathlib import Path

from pypdf import PdfReader
import pypdfium2 as pdfium
import zxingcpp

OUT=Path(__file__).resolve().parents[2]/'test-results/production-acceptance'
evidence=json.loads((OUT/'linux.json').read_text())
legacy,current=[line.split('\t') for line in evidence['browser_rows']]
tag=current[1]
amount='$'+current[2]
with pdfium.PdfDocument(OUT/'release-tag.pdf') as doc:
    assert len(doc)==1
    page=doc[0]
    contents=page.get_textpage().get_text_bounded()
    assert all(v in contents for v in (tag,amount,'STORE '+current[5].zfill(3)))
    decoded=zxingcpp.read_barcodes(page.render(scale=203/72).to_pil(),formats=zxingcpp.BarcodeFormat.Code128)
    assert [barcode.text for barcode in decoded]==[tag]
report='\n'.join(p.extract_text() for p in PdfReader(OUT/'report-export.pdf').pages)
assert '\n2\n$15.25\n0.0% (0)\n2\n' in report
assert f'Fixture staff_e2e\nTest North\n1\n{amount}' in report
assert f'Fixture other_e2e\nUnknown\n1\n${legacy[2]}' in report
print('PASS report amounts, staff and store attribution, label content and 203 dpi Code 128 decode')
