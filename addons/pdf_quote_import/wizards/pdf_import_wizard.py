from odoo import models, fields, api
from odoo.exceptions import UserError


class PdfImportWizard(models.TransientModel):
    _name = 'pdf.import.wizard'
    _description = 'PDF Quote Import Wizard'

    pdf_file = fields.Binary(string='PDF File', required=True)
    filename = fields.Char(string='Filename')
    partner_id = fields.Many2one('res.partner', string='Customer', 
                                  help='Select customer if known, otherwise leave empty')
    notes = fields.Text(string='Additional Notes')

    def action_import(self):
        """Process PDF import"""
        self.ensure_one()
        
        if not self.pdf_file:
            raise UserError('Please upload a PDF file.')
        
        # Create PDF quote import record
        pdf_import = self.env['pdf.quote.import'].create({
            'pdf_file': self.pdf_file,
            'filename': self.filename,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'notes': self.notes,
        })
        
        # Process the import
        return pdf_import.action_import()
