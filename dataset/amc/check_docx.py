"""Local DOCX content checks; run separately from built-in benchmark checks.

Usage: .venv/bin/python dataset/amc/check_docx.py CASE_DIR OUTPUT.docx
Case document_checks holds the required sections and representative source terms.
"""
import argparse
import json
import re
import unicodedata
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import yaml

NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def normalize(text):
    text = unicodedata.normalize('NFKC', text).casefold().replace('ё', 'е')
    text = re.sub(r'[−–—…]', '-', text)
    text = text.replace('×', 'x')
    return re.sub(r'\s+', '', text)


def check_document(case_dir, document):
    specs = yaml.safe_load((case_dir / 'case.yaml').read_text())['document_checks']
    records = []
    def record(name, passed, evidence):
        records.append(dict(name=name, passed=bool(passed), category='document',
                            severity='major', evidence=evidence))
    try:
        with ZipFile(document) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
            ET.fromstring(archive.read('[Content_Types].xml'))
            if archive.testzip():
                raise ValueError('Corrupt DOCX member')
        paragraphs = [''.join(t.text or '' for t in p.iter(NS + 't'))
                      for p in root.iter(NS + 'p')]
        text = normalize('\n'.join(paragraphs))
        record('docx_valid', bool(text), 'Readable non-empty DOCX')
        # Compare complete heading paragraphs, tolerating numbering and whitespace.
        headings = [normalize(re.sub(r'^\s*\d+(?:\.\d+)*[.)]?\s*', '', p))
                    for p in paragraphs]
        for section in specs['required_sections']:
            title = re.sub(r'^\d+\.\s*', '', section)
            record('section:' + title, normalize(title) in headings, title)
        for term in specs['required_terms']:
            record('term:' + term, normalize(term) in text, term)
        for title in specs['forbidden_sections']:
            record('excluded_section:' + title, normalize(title) not in headings, title)
    except (OSError, ValueError, KeyError, ET.ParseError, BadZipFile) as exc:
        record('docx_valid', False, str(exc))
    return {'passed': sum(r['passed'] for r in records),
            'failed': sum(not r['passed'] for r in records), 'checks': records}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case_dir', type=Path)
    parser.add_argument('document', type=Path)
    args = parser.parse_args()
    result = check_document(args.case_dir, args.document)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result['failed']))
