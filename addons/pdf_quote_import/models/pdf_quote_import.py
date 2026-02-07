from odoo import models, fields, api
import base64
import logging

_logger = logging.getLogger(__name__)

try:
    import PyPDF2
except ImportError:
    _logger.warning('PyPDF2 library is not installed. PDF parsing will not work.')
    PyPDF2 = None


class PdfQuoteImport(models.Model):
    _name = 'pdf.quote.import'
    _description = 'PDF Quote Import'
    _order = 'create_date desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='New')
    pdf_file = fields.Binary(string='PDF File', required=True, attachment=True)
    filename = fields.Char(string='Filename')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('imported', 'Imported'),
        ('failed', 'Failed'),
    ], string='Status', default='draft', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Customer')
    sale_order_id = fields.Many2one('sale.order', string='Created Sale Order', readonly=True)
    extracted_text = fields.Text(string='Extracted Text', readonly=True)
    notes = fields.Text(string='Notes')
    error_message = fields.Text(string='Error Message', readonly=True)

    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('pdf.quote.import') or 'New'
        return super(PdfQuoteImport, self).create(vals)

    def extract_pdf_text(self):
        """Extract text from PDF file"""
        self.ensure_one()
        if not PyPDF2:
            raise ImportError('PyPDF2 library is required for PDF parsing. Please install it.')
        
        try:
            pdf_data = base64.b64decode(self.pdf_file)
            pdf_reader = PyPDF2.PdfReader(pdf_data)
            
            text_content = []
            for page in pdf_reader.pages:
                text_content.append(page.extract_text())
            
            extracted_text = '\n'.join(text_content)
            self.write({'extracted_text': extracted_text})
            return extracted_text
        except Exception as e:
            error_msg = f'Failed to extract PDF: {str(e)}'
            self.write({'error_message': error_msg, 'state': 'failed'})
            raise

    def parse_quote_data(self, text):
        """Parse quote data from extracted text
        Override this method to implement custom parsing logic"""
        # Basic implementation - can be extended based on specific PDF format
        quote_data = {
            'partner_id': self.partner_id.id if self.partner_id else False,
            'note': text[:500] if text else '',  # First 500 chars as note
        }
        return quote_data

    def action_import(self):
        """Import PDF and create sale order"""
        self.ensure_one()
        
        try:
            # Extract text from PDF
            extracted_text = self.extract_pdf_text()
            
            # Parse the data
            quote_data = self.parse_quote_data(extracted_text)
            
            # Create sale order
            sale_order = self.env['sale.order'].create(quote_data)
            
            self.write({
                'sale_order_id': sale_order.id,
                'state': 'imported',
            })
            
            return {
                'type': 'ir.actions.act_window',
                'name': 'Sale Order',
                'res_model': 'sale.order',
                'res_id': sale_order.id,
                'view_mode': 'form',
                'target': 'current',
            }
        except Exception as e:
            error_msg = f'Import failed: {str(e)}'
            self.write({'error_message': error_msg, 'state': 'failed'})
            raise
