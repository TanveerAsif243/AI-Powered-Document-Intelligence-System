# Evaluation report (measured)

Dataset: 31 files, 56 labelled invoices, 4 deliberately corrupted files. LLM used: **False**.

## Extraction accuracy (field-level, exact/normalised match; a value invented for an empty field counts as wrong)

| Field | Accuracy % |
|---|---|
| invoice_number | 21.43 |
| invoice_date | 21.43 |
| supplier_name | 21.43 |
| customer_name | 21.43 |
| subtotal | 21.43 |
| tax | 21.43 |
| total | 21.43 |
| currency | 21.43 |

**Overall field accuracy:** 21.43%  
**Invoices with every field correct:** 21.43%  
**Line-item amount F1:** 69.33%

## Accuracy by source

| Source | Field accuracy % | Mean s/file |
|---|---|---|
| digital_pdf | 0.0 | 0.01 |
| digital_pdf_multi | 0.0 | 0.01 |
| scan_png | 0.0 | 0.09 |
| scan_pdf | 0.0 | 0.03 |
| scan_jpg | 0.0 | 0.08 |
| scan_tiff | 0.0 | 0.4 |
| ledger_xlsx | 0.0 | 0.05 |
| ledger_csv | 100.0 | 0.08 |

## Reliability and speed

- Document success rate: **7.41%**
- Document failure rate (valid files that failed): **92.59%**
- Corrupted files rejected cleanly (no crash): **100.0%**
- Processing time per file: mean 0.04s, p50 0.01s, p95 0.13s (total 1.3s, single process)

## Validation (flagging for manual review)

- Precision: **100.0%**, recall: **100.0%** (TP 3, FP 0, FN 0, TN 9)
- Injected arithmetic/missing-field errors detected with the correct code: **100.0%**
- Extraction mistakes caught by review flags: **None%** (0 wrong invoices, 0 silently accepted)

## Per file

| File | Status | Seconds |
|---|---|---|
| digital_00.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.0 |
| digital_01.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.01 |
| digital_02.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.02 |
| digital_03.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.01 |
| digital_04.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.0 |
| digital_05.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.01 |
| digital_06.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.0 |
| digital_07.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.01 |
| digital_multi_00.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.01 |
| digital_multi_01.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.0 |
| digital_multi_02.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.01 |
| scan_clean_00.png | FAILED - OCRUnavailableError: pytesseract is not installed | 0.13 |
| scan_clean_01.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.02 |
| scan_clean_02.jpg | FAILED - OCRUnavailableError: pytesseract is not installed | 0.05 |
| scan_noisy_03.png | FAILED - OCRUnavailableError: pytesseract is not installed | 0.04 |
| scan_noisy_04.tiff | FAILED - OCRUnavailableError: pytesseract is not installed | 0.4 |
| scan_noisy_05.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.04 |
| scan_noisy_06.jpg | FAILED - OCRUnavailableError: pytesseract is not installed | 0.09 |
| scan_poor_07.png | FAILED - OCRUnavailableError: pytesseract is not installed | 0.1 |
| scan_poor_08.jpg | FAILED - OCRUnavailableError: pytesseract is not installed | 0.09 |
| scan_poor_09.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.02 |
| ledger_00.xlsx | FAILED - UnsupportedFileError: Missing spreadsheet engine: `Import openpyxl` failed.  Use pip or conda to install the openpyxl package. | 0.09 |
| ledger_01.xlsx | FAILED - UnsupportedFileError: Missing spreadsheet engine: `Import openpyxl` failed.  Use pip or conda to install the openpyxl package. | 0.0 |
| ledger_02.csv | ok | 0.11 |
| ledger_03.csv | ok | 0.04 |
| dup_a.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.0 |
| dup_b.pdf | FAILED - CorruptedFileError: Unreadable PDF: No module named 'pypdf' | 0.0 |
| corrupt_truncated.pdf | failed_as_expected | 0.0 |
| corrupt_garbage.pdf | failed_as_expected | 0.0 |
| corrupt_empty.png | failed_as_expected | 0.0 |
| corrupt_bad.xlsx | failed_as_expected | 0.0 |
