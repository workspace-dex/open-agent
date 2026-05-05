---
name: office
description: Office document tools - docx, xlsx, pptx, pdf creation/editing
version: 1.0.0
author: anthropic skills + open-agent
license: MIT
triggers:
  - word
  - document
  - docx
  - spreadsheet
  - excel
  - xlsx
  - presentation
  - slides
  - pptx
  - pdf
conditions:
  - docx
  - word doc
  - excel
  - spreadsheet
  - pptx
  - slides
  - pdf file
---

# Office Documents Skill

## Overview

Process Word, Excel, PowerPoint, and PDF files using command-line tools.

## PDF (pdf)

```bash
# Install
pip install pypdf pdfplumber

# Extract text
python -c "
from pypdf import PdfReader
r = PdfReader('file.pdf')
print(f'Pages: {len(r.pages)}')
for p in r.pages:
    print(p.extract_text()[:500])
"

# Merge PDFs
python -c "
from pypdf import PdfWriter, PdfReader
w = PdfWriter()
for f in ['a.pdf', 'b.pdf']:
    r = PdfReader(f)
    for p in r.pages: w.add_page(p)
with open('merged.pdf', 'wb') as out: w.write(out)
"
```

## Excel (xlsx)

```bash
# Install
pip install openpyxl pandas

# Create spreadsheet
python -c "
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
ws['A1'] = 'Name'
ws['B1'] = 'Value'
ws['A2'] = 'Item 1'
ws['B2'] = 100
wb.save('data.xlsx')
"

# Read with pandas
python -c "
import pandas as pd
df = pd.read_excel('data.xlsx')
print(df)
"
```

## Word (docx)

```bash
# Install
pip install python-docx

# Create document
python -c "
from docx import Document
doc = Document()
doc.add_heading('Title', 0)
doc.add_paragraph('Content here')
doc.save('doc.docx')
"
```

## PowerPoint (pptx)

```bash
# Install  
pip install python-pptx

# Create presentation
python -c "
from pptx import Presentation
prs = Presentation()
slide = prs.slides.add_slide(prs.slide_layouts[0])
title = slide.shapes.title
title.text = 'Title'
prs.save('deck.pptx')
"