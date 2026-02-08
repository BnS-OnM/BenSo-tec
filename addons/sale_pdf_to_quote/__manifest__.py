# -*- coding: utf-8 -*-
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).
{
    'name': 'Sale PDF to Quote',
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'summary': 'Import PDF files to create sales quotations automatically',
    'description': """
        This module allows users to upload PDF files and automatically create
        sales quotations with order lines. It supports two different PDF layouts
        with auto-detection and intelligent product matching.
    """,
    'author': 'BenSo-tec',
    'website': 'https://github.com/BnS-OnM/BenSo-tec',
    'license': 'LGPL-3',
    'depends': [
        'sale',
        'product',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/pdf_to_quote_wizard_views.xml',
        'views/sale_pdf_menu.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
