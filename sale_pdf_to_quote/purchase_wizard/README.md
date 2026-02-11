# Purchase PDF to Order Wizard

## Description

This module allows you to import product information from PDF files and create purchase orders directly in Odoo without any intermediate XLSX export/import steps.

## Features

- **Upload PDF Files**: Simple wizard interface for uploading PDF files
- **Dual Format Support**: Automatic detection and parsing of 2 PDF formats:
  - **Bottom Bar Schema**: PDFs with "Appliances:" and "Controls:" sections
  - **Table Format**: Quotation PDFs with article numbers, quantities, descriptions, and prices
- **Smart Product Matching**: 
  - Primary matching by product code (default_code)
  - Fallback to fuzzy name matching (≥80% similarity)
  - Support for various code formats (VR, VRC, VWL, VIH, VP RW series)
- **Direct Purchase Order Creation**: Creates purchase.order with order lines directly from PDF
- **Flexible Pricing**: Option to use prices from PDF or let Odoo determine prices
- **Duplicate Handling**: Automatically combines quantities for duplicate products
- **Detailed Summary**: Shows matched and unmatched items with best candidates

## Usage

### Accessing the Wizard

1. Navigate to **Purchase > Orders > Import PDF to Purchase Order**
2. Or from the Purchase Orders list view, use the action menu

### Using the Wizard

1. **Upload PDF File**: Select your PDF file
2. **Select Vendor**: Choose the vendor for this purchase order (required)
3. **Use PDF Prices**: 
   - Enable to use prices from PDF (when available)
   - Disable to let Odoo determine prices
4. **Reference Prefix**: Set a prefix for traceability (default: "GPT-001")
5. Click **"Create Purchase Order"** to create the order

### After Processing

The wizard will show:
- Link to the created purchase order
- Number of matched product lines
- Number of unmatched items
- Details of unmatched items with best match candidates

## Key Differences from Sale Wizard

The purchase wizard uses purchase.order.line field names:
- `product_qty` instead of `product_uom_qty` for quantity
- `product_uom_id` for unit of measure (same as sale)
- `partner_id` refers to vendor instead of customer

## Technical Details

- **Model**: `purchase.pdf.to.order.wizard` (TransientModel)
- **Dependencies**: PyPDF2 (required), rapidfuzz (optional but recommended)
- **Odoo Compatibility**: Designed for Odoo 16/17/19
- **Security**: Accessible by Purchase > User and Purchase > Manager groups

## Bug Fix

This module was created to fix the error:
```
ValueError: Invalid field 'product_uom' on model 'purchase.order.line'
```

The fix ensures the correct field name `product_uom_id` is used (not `product_uom`) when creating purchase order lines.
