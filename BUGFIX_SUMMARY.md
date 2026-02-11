# Fix Summary: RPC Error - Invalid field 'product_uom'

## Problem Statement

**Error**: `ValueError: Invalid field 'product_uom' on model 'purchase.order.line'`

**Location**: `/home/odoo/src/user/sale_pdf_to_quote/purchase_wizard/purchase_pdf_to_order_wizard.py` line 483

**Traceback**:
```python
KeyError: 'product_uom'
ValueError: Invalid field 'product_uom' on model 'purchase.order.line'
```

The error occurred when trying to create purchase order lines from PDF import.

## Root Cause

The purchase wizard was using an incorrect field name when creating purchase order lines:
- **Incorrect**: `'product_uom'` (field does not exist)
- **Correct**: `'product_uom_id'` (Many2one field to product.uom)

In Odoo, relational fields (Many2one, Many2many, One2many) must end with `_id` or `_ids` suffix. The field `product_uom` does not exist on the `purchase.order.line` model.

## Solution

Created the purchase wizard module with correct field names:

### Key Changes in `purchase_pdf_to_order_wizard.py` (line 583):

```python
line_vals = {
    "order_id": purchase_order.id,
    "product_id": product.id,
    "product_qty": line_data["qty"],              # Correct: purchase uses product_qty
    "product_uom_id": product.uom_id.id,          # FIXED: Using product_uom_id instead of product_uom
    "name": line_data["desc"],
}
```

### Field Name Differences

| Model | Quantity Field | UOM Field |
|-------|----------------|-----------|
| `sale.order.line` | `product_uom_qty` | `product_uom_id` |
| `purchase.order.line` | `product_qty` | `product_uom_id` |

## Files Created/Modified

### New Files:
1. `sale_pdf_to_quote/purchase_wizard/__init__.py` - Module initialization
2. `sale_pdf_to_quote/purchase_wizard/purchase_pdf_to_order_wizard.py` - Main wizard logic
3. `sale_pdf_to_quote/purchase_wizard/README.md` - Documentation
4. `sale_pdf_to_quote/views/purchase_pdf_to_order_wizard_views.xml` - UI definition

### Modified Files:
1. `sale_pdf_to_quote/__init__.py` - Import purchase_wizard
2. `sale_pdf_to_quote/__manifest__.py` - Add purchase dependency and view files
3. `sale_pdf_to_quote/security/ir.model.access.csv` - Add access rights
4. `sale_pdf_to_quote/README.md` - Updated documentation

## Testing

### Manual Verification:
- ✅ Correct field name `product_uom_id` used (line 583)
- ✅ Correct quantity field `product_qty` used (line 582)
- ✅ No occurrences of incorrect `product_uom` field name
- ✅ Sale wizard remains unchanged and correct

### Code Review:
- ✅ All review comments addressed
- ✅ Dutch error message translated to English
- ✅ Misleading line number comment fixed

### Security Scan:
- ✅ CodeQL scan: **0 alerts** - No vulnerabilities found

## Impact

This fix resolves the RPC error that prevented users from creating purchase orders via PDF import. The purchase wizard now correctly creates purchase.order.line records with proper field names that match Odoo's standard model structure.

## Version

Module version updated to `19.0.2.0.0` to reflect the bug fix and new purchase wizard functionality.
