{
    'name': 'PDF to Sales and Purchase',
    'version': '19.0.2.0.0',
    'category': 'Sales',
    'summary': 'Import PDF files and create sales quotations and purchase orders directly',
    'description': """
        PDF to Sales Quotation and Purchase Order Module
        =================================================
        This module allows you to import product information from PDF files
        and create sales quotations or purchase orders directly in Odoo.

        Features:
        ---------
        * Upload PDF files via wizard interface
        * Support for 2 PDF formats with auto-detection:
          - Bottom bar schema (Appliances/Controls format)
          - Table format (quotation with article numbers)
        * Automatic product matching by code and fuzzy name matching
        * Direct creation of sale.order or purchase.order with order lines
        * Summary of matched and unmatched items
        * No XLSX export/import required

        Technical:
        ----------
        * Uses PyPDF2 for PDF text extraction
        * Uses rapidfuzz or difflib for fuzzy matching
        * Supports Odoo 16/17/19
    """,
    'author': 'BenSo-tec',
    'website': 'https://github.com/BnS-OnM/BenSo-tec',
    'depends': ['sale', 'purchase', 'product'],
    'external_dependencies': {
        'python': ['PyPDF2', 'rapidfuzz'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/pdf_to_quote_wizard_views.xml',
        'views/purchase_pdf_to_order_wizard_views.xml',
        'views/sale_pdf_menu.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
