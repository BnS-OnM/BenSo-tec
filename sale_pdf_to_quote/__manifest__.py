{
    'name': 'PDF to Sales Quotation',
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'summary': 'Import PDF files and create sales quotations directly',
    'description': """
        PDF to Sales Quotation Module
        ==============================
        This module allows you to import product information from PDF files
        and create sales quotations directly in Odoo.
        
        Features:
        ---------
        * Upload PDF files via wizard interface
        * Support for 2 PDF formats with auto-detection:
          - Bottom bar schema (Appliances/Controls format)
          - Table format (quotation with article numbers)
        * Automatic product matching by code and fuzzy name matching
        * Direct creation of sale.order with order lines
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
    'depends': ['sale', 'product'],
    'external_dependencies': {
        'python': ['PyPDF2'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/pdf_to_quote_wizard_views.xml',
        'views/sale_pdf_menu.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
