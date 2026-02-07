from odoo.tests import TransactionCase
from odoo.exceptions import UserError
import base64


class TestPdfQuoteImport(TransactionCase):
    
    def setUp(self):
        super(TestPdfQuoteImport, self).setUp()
        self.PdfQuoteImport = self.env['pdf.quote.import']
        self.PdfImportWizard = self.env['pdf.import.wizard']
        self.partner = self.env['res.partner'].create({
            'name': 'Test Customer',
        })
    
    def test_create_pdf_quote_import(self):
        """Test creating a PDF quote import record"""
        # Create a dummy PDF file (just base64 encoded text for testing)
        dummy_pdf = base64.b64encode(b'Dummy PDF content')
        
        pdf_import = self.PdfQuoteImport.create({
            'pdf_file': dummy_pdf,
            'filename': 'test_quote.pdf',
            'partner_id': self.partner.id,
        })
        
        self.assertTrue(pdf_import)
        self.assertEqual(pdf_import.state, 'draft')
        self.assertEqual(pdf_import.partner_id, self.partner)
        self.assertTrue(pdf_import.name.startswith('PDF-'))
    
    def test_wizard_creation(self):
        """Test PDF import wizard creation"""
        dummy_pdf = base64.b64encode(b'Dummy PDF content')
        
        wizard = self.PdfImportWizard.create({
            'pdf_file': dummy_pdf,
            'filename': 'test_quote.pdf',
            'partner_id': self.partner.id,
        })
        
        self.assertTrue(wizard)
        self.assertEqual(wizard.partner_id, self.partner)
    
    def test_wizard_no_file_error(self):
        """Test wizard validation when no file is uploaded"""
        wizard = self.PdfImportWizard.create({
            'pdf_file': False,
            'partner_id': self.partner.id,
        })
        
        with self.assertRaises(UserError):
            wizard.action_import()
