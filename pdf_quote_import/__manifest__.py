{
    'name': 'PDF Quote Import',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Import quotes from PDF files',
    'description': """
        PDF Quote Import Module
        =======================
        This module allows you to import quotes from PDF files into Odoo.
        
        Features:
        ---------
        * Upload PDF quote files
        * Extract quote information from PDF
        * Create sale orders/quotations from PDF data
        * Simple wizard interface for easy import
    """,
    'author': 'BenSo-tec',
    'website': 'https://github.com/BnS-OnM/BenSo-tec',
    'depends': ['base', 'sale_management'],
    'data': [
        'security/ir.model.access.csv',
        'wizards/pdf_import_wizard_views.xml',
        'views/pdf_quote_import_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
