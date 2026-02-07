# PDF Quote Import Module

## Overview

The PDF Quote Import module for Odoo allows you to import quotes from PDF files and automatically create sale orders/quotations. This module simplifies the process of converting PDF quotes into Odoo sale orders.

## Features

- **Easy PDF Upload**: Simple wizard interface for uploading PDF files
- **Automatic Text Extraction**: Extracts text content from PDF files using PyPDF2
- **Sale Order Creation**: Automatically creates sale orders from imported PDF data
- **Customer Association**: Link imported quotes to existing customers
- **Import History**: Track all PDF imports with status tracking (Draft, Imported, Failed)
- **Error Handling**: Comprehensive error tracking and reporting

## Installation

### Prerequisites

This module requires the PyPDF2 Python library. Install it using pip:

```bash
pip install PyPDF2
```

### Installing the Module

1. Copy the `pdf_quote_import` folder to your Odoo addons directory
2. Update the addons list in Odoo
3. Install the module from Apps menu

## Usage

### Importing a PDF Quote

1. Navigate to **Sales > PDF Quote Import > Import PDF Quote**
2. Upload your PDF file
3. (Optional) Select a customer
4. (Optional) Add notes
5. Click **Import** button
6. The module will extract text from the PDF and create a sale order
7. You'll be redirected to the newly created sale order

### Viewing Import History

1. Navigate to **Sales > PDF Quote Import > PDF Imports**
2. View all your PDF imports with their status
3. Click on any import to view details, extracted text, and any errors

## Configuration

### Customizing PDF Parsing

The module includes a basic PDF text extraction implementation. To customize the parsing logic for your specific PDF format:

1. Inherit the `pdf.quote.import` model
2. Override the `parse_quote_data` method
3. Implement custom logic to extract specific fields from the PDF text

Example:

```python
from odoo import models

class PdfQuoteImportCustom(models.Model):
    _inherit = 'pdf.quote.import'
    
    def parse_quote_data(self, text):
        quote_data = super().parse_quote_data(text)
        # Add your custom parsing logic here
        # Extract customer name, products, prices, etc.
        return quote_data
```

## Technical Details

### Models

- **pdf.quote.import**: Main model for storing PDF import records
  - Stores PDF file, extracted text, and import status
  - Links to created sale orders
  - Tracks errors if import fails

- **pdf.import.wizard**: Transient model for the import wizard
  - Handles PDF file upload
  - Processes import and creates records

### Dependencies

- base
- sale_management
- PyPDF2 (Python library)

## Security

Access rights are defined for:
- **Sales User**: Can read, write, and create PDF imports
- **Sales Manager**: Full access including delete permissions

## License

LGPL-3

## Author

BenSo-tec

## Support

For issues and questions, please visit: https://github.com/BnS-OnM/BenSo-tec
