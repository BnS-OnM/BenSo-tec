# -*- coding: utf-8 -*-
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

import base64
import re
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SalePdfToQuoteWizard(models.TransientModel):
    _name = 'sale.pdf.to.quote.wizard'
    _description = 'PDF to Sales Quote Wizard'

    pdf_file = fields.Binary(string='PDF File', required=True)
    filename = fields.Char(string='Filename')
    partner_id = fields.Many2one('res.partner', string='Customer', required=True)
    use_pdf_prices = fields.Boolean(
        string='Use PDF Prices',
        default=True,
        help='If True and price is present on line, use that price. '
             'Otherwise leave price empty so Odoo takes the price from pricelist.'
    )
    name_prefix = fields.Char(
        string='Name Prefix',
        default='GPT-001',
        help='Will be set in client_order_ref or origin for traceability'
    )
    
    # Result fields
    sale_order_id = fields.Many2one('sale.order', string='Created Quotation', readonly=True)
    matched_count = fields.Integer(string='Matched Lines', readonly=True)
    unmatched_count = fields.Integer(string='Unmatched Lines', readonly=True)
    unmatched_text = fields.Text(string='Unmatched Details', readonly=True)

    def _extract_text_from_pdf(self, pdf_bytes):
        """Extract text from PDF using pypdf."""
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader
            except ImportError:
                raise UserError(_('pypdf or PyPDF2 library is required. Please install it.'))
        
        try:
            import io
            pdf_file = io.BytesIO(pdf_bytes)
            reader = PdfReader(pdf_file)
            
            text = ''
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + '\n'
            
            return text.strip()
        except Exception as e:
            _logger.error(f'Error extracting text from PDF: {e}')
            raise UserError(_('Failed to extract text from PDF: %s') % str(e))

    def _detect_pdf_type(self, text):
        """Detect PDF type: 'table' or 'bottom_bar'."""
        text_lower = text.lower()
        
        # Check for table type indicators
        has_artikel_nr = 'artikel nr' in text_lower or 'artikelnr' in text_lower
        has_unit_price = 'unit price' in text_lower or 'eenheidsprijs' in text_lower
        
        # Check for table pattern: artikelnummer (5+ digits) followed by qty
        table_pattern = re.compile(r'^\*?\d{5,}\s+\d+\s+', re.MULTILINE)
        table_matches = len(table_pattern.findall(text))
        
        # Check for bottom_bar type indicators
        has_appliances = 'Appliances:' in text
        has_controls = 'Controls:' in text
        
        # Decision logic
        if has_artikel_nr or has_unit_price or table_matches >= 2:
            return 'table'
        elif has_appliances or has_controls:
            return 'bottom_bar'
        else:
            # No match found
            lines = text.split('\n')[:30]
            sample_text = '\n'.join(lines)
            raise UserError(_(
                'Could not detect PDF type. The PDF does not match known formats.\n\n'
                'First 30 lines:\n%s'
            ) % sample_text)

    def _parse_eu_number(self, text):
        """Parse European number format (1.234,56) to float."""
        if not text:
            return None
        try:
            # Remove spaces and dots (thousand separators), replace comma with dot
            text = text.strip().replace(' ', '').replace('.', '').replace(',', '.')
            return float(text)
        except (ValueError, AttributeError):
            return None

    def _parse_table_lines(self, text):
        """Parse table-type PDF lines.
        
        Returns list of dicts with keys: code, qty, desc, unit_price
        """
        results = []
        lines = text.split('\n')
        
        # Pattern: artikelnummer (optionally with *), qty, description, optional prices
        # Example: "956395 1 AROTHERM SPLIT PLUS ... 5.268,00 5.268,00"
        # Example: "*208070 1 SCHROEFCILINDER M8 0,50 0,50"
        pattern = re.compile(r'^(\*?)(\d{5,})\s+(\d+)\s+(.+?)(?:\s+([\d.,]+)\s+([\d.,]+))?$')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            match = pattern.match(line)
            if match:
                star, code, qty_str, description, unit_price_str, total_price_str = match.groups()
                
                code = code.strip()
                qty = int(qty_str) if qty_str else 1
                desc = description.strip()
                unit_price = self._parse_eu_number(unit_price_str) if unit_price_str else None
                
                results.append({
                    'code': code,
                    'qty': qty,
                    'desc': desc,
                    'unit_price': unit_price,
                })
        
        _logger.info(f'Parsed {len(results)} table lines')
        return results

    def _parse_bottom_bar(self, text):
        """Parse bottom_bar-type PDF.
        
        Returns list of dicts with keys: codes, desc
        """
        results = []
        
        # Find Appliances: and Controls: sections
        for section in ['Appliances:', 'Controls:']:
            if section not in text:
                continue
            
            # Extract text after the section marker
            start_idx = text.index(section) + len(section)
            # Find the end (next section or end of text)
            remaining_text = text[start_idx:]
            
            # Take until next section marker or newline (simple approach)
            # More robust: take the rest of the line
            end_idx = remaining_text.find('\n')
            if end_idx == -1:
                section_text = remaining_text
            else:
                section_text = remaining_text[:end_idx]
            
            # Split by comma
            items = [item.strip() for item in section_text.split(',') if item.strip()]
            
            for item in items:
                # Try to extract codes from item
                codes = self._extract_codes_from_text(item)
                results.append({
                    'codes': codes,
                    'desc': item,
                })
        
        _logger.info(f'Parsed {len(results)} bottom_bar items')
        return results

    def _extract_codes_from_text(self, text):
        """Extract product codes from text using regex patterns.
        
        Returns list of extracted codes.
        """
        codes = []
        
        # VR patterns: VR71, VR940
        vr_pattern = r'\bVR\d{2,4}\b'
        codes.extend(re.findall(vr_pattern, text))
        
        # VRC patterns: VRC720 (with optional space)
        vrc_pattern = r'\bVRC\s*(\d{2,4})\b'
        vrc_matches = re.findall(vrc_pattern, text)
        codes.extend(['VRC' + m for m in vrc_matches])
        
        # VWL patterns: VWL 8.2 AS / IS
        vwl_pattern = r'\bVWL\s*(\d+(?:\.\d+)?)\s*([A-Z]{1,4})\b'
        vwl_matches = re.findall(vwl_pattern, text)
        for num, suffix in vwl_matches:
            codes.append(f'VWL{num}{suffix}')
        
        # VIH patterns: VIH RW
        vih_pattern = r'\bVIH\s*([A-Z]{1,4})\b'
        vih_matches = re.findall(vih_pattern, text)
        codes.extend(['VIH' + m for m in vih_matches])
        
        # VP patterns: VP RW 45/2 B
        vp_pattern = r'\bVP\s*RW\s*(\d+/\d+)\s*([A-Z])\b'
        vp_matches = re.findall(vp_pattern, text)
        for frac, suffix in vp_matches:
            codes.append(f'VPRW{frac}{suffix}')
        
        return codes

    def _match_product(self, code=None, desc=None):
        """Match product by code or description.
        
        Returns tuple: (product_record, score, method, candidate_name)
        - product_record: product.product record or False
        - score: match score (0-100)
        - method: 'code_exact', 'code_template', 'fuzzy_name'
        - candidate_name: name of the matched product or best candidate
        """
        Product = self.env['product.product']
        ProductTemplate = self.env['product.template']
        
        # Try exact code match on product.product
        if code:
            product = Product.search([('default_code', '=', code)], limit=1)
            if product:
                return product, 100, 'code_exact', product.display_name
            
            # Try code match on product.template
            template = ProductTemplate.search([('default_code', '=', code)], limit=1)
            if template:
                # Get a variant
                product = Product.search([('product_tmpl_id', '=', template.id)], limit=1)
                if product:
                    return product, 100, 'code_template', product.display_name
        
        # Fuzzy name matching
        if desc:
            return self._fuzzy_match_product(desc)
        
        return False, 0, 'no_match', ''

    def _fuzzy_match_product(self, desc):
        """Fuzzy match product by description.
        
        Returns tuple: (product_record, score, method, candidate_name)
        """
        try:
            from rapidfuzz import fuzz
            use_rapidfuzz = True
        except ImportError:
            from difflib import SequenceMatcher
            use_rapidfuzz = False
        
        Product = self.env['product.product']
        
        # Extract tokens for ORM filtering (2-4 tokens)
        tokens = desc.split()[:4]
        domain = ['|', '|']
        for token in tokens[:3]:
            domain.extend([('name', 'ilike', token), ('default_code', 'ilike', token)])
        
        # Limit candidates for performance
        candidates = Product.search(domain, limit=100)
        
        if not candidates:
            # Fallback: search without domain
            candidates = Product.search([], limit=100)
        
        best_score = 0
        best_product = False
        best_name = ''
        
        for product in candidates:
            product_name = product.display_name or product.name
            
            if use_rapidfuzz:
                score = fuzz.ratio(desc.lower(), product_name.lower())
            else:
                score = SequenceMatcher(None, desc.lower(), product_name.lower()).ratio() * 100
            
            if score > best_score:
                best_score = score
                best_product = product
                best_name = product_name
        
        if best_score >= 80:
            return best_product, best_score, 'fuzzy_name', best_name
        else:
            return False, best_score, 'fuzzy_name', best_name

    def _create_sale_order(self, matched_lines):
        """Create sale.order with matched lines.
        
        matched_lines: list of dicts with keys:
            - product_id: product.product record
            - qty: quantity
            - desc: description
            - unit_price: optional price
        """
        # Combine duplicate products
        combined_lines = {}
        for line in matched_lines:
            product_id = line['product_id'].id
            if product_id in combined_lines:
                combined_lines[product_id]['qty'] += line['qty']
            else:
                combined_lines[product_id] = line.copy()
        
        # Create sale.order
        order_vals = {
            'partner_id': self.partner_id.id,
            'client_order_ref': f"{self.name_prefix} - {self.filename or 'PDF'}",
        }
        order = self.env['sale.order'].create(order_vals)
        
        # Create order lines
        for line_data in combined_lines.values():
            product = line_data['product_id']
            line_vals = {
                'order_id': order.id,
                'product_id': product.id,
                'product_uom_qty': line_data['qty'],
                'product_uom': product.uom_id.id,
            }
            
            # Use parsed description if available
            if line_data.get('desc'):
                line_vals['name'] = line_data['desc']
            
            # Set price if use_pdf_prices and price available
            if self.use_pdf_prices and line_data.get('unit_price') is not None:
                line_vals['price_unit'] = line_data['unit_price']
            
            self.env['sale.order.line'].create(line_vals)
        
        _logger.info(f'Created sale.order {order.name} with {len(combined_lines)} lines')
        return order

    def action_create_quotation(self):
        """Main action: parse PDF and create quotation."""
        self.ensure_one()
        
        # Decode PDF
        pdf_bytes = base64.b64decode(self.pdf_file)
        
        # Extract text
        text = self._extract_text_from_pdf(pdf_bytes)
        
        if not text or len(text) < 10:
            raise UserError(_(
                'PDF contains no readable text (probably a scan). '
                'Please upload a text-based PDF.'
            ))
        
        # Detect PDF type
        pdf_type = self._detect_pdf_type(text)
        _logger.info(f'Detected PDF type: {pdf_type}')
        
        # Parse based on type
        if pdf_type == 'table':
            parsed_items = self._parse_table_lines(text)
        else:  # bottom_bar
            parsed_items = self._parse_bottom_bar(text)
        
        # Match products
        matched_lines = []
        unmatched_lines = []
        
        for item in parsed_items:
            if pdf_type == 'table':
                code = item.get('code')
                desc = item.get('desc')
                qty = item.get('qty', 1)
                unit_price = item.get('unit_price')
            else:  # bottom_bar
                # Try each extracted code
                code = None
                codes = item.get('codes', [])
                desc = item.get('desc')
                qty = 1
                unit_price = None
                
                # Try matching with each code
                matched = False
                for c in codes:
                    product, score, method, candidate_name = self._match_product(code=c, desc=desc)
                    if product:
                        matched_lines.append({
                            'product_id': product,
                            'qty': qty,
                            'desc': desc,
                            'unit_price': unit_price,
                        })
                        _logger.debug(f'Matched: {desc} -> {candidate_name} (method: {method}, score: {score})')
                        matched = True
                        break
                
                if matched:
                    continue
                
                # If no code matched, try fuzzy on description
                if not codes:
                    code = None
            
            # Match product
            if pdf_type == 'table' or not matched:
                product, score, method, candidate_name = self._match_product(code=code, desc=desc)
                
                if product:
                    matched_lines.append({
                        'product_id': product,
                        'qty': qty,
                        'desc': desc,
                        'unit_price': unit_price,
                    })
                    _logger.debug(f'Matched: {desc} -> {candidate_name} (method: {method}, score: {score})')
                else:
                    unmatched_lines.append({
                        'raw': desc or code or 'Unknown',
                        'type': pdf_type,
                        'score': score,
                        'candidate': candidate_name or 'None',
                    })
                    _logger.info(f'Unmatched: {desc or code} (best: {candidate_name}, score: {score})')
        
        # Create sale order if we have matched lines
        if matched_lines:
            order = self._create_sale_order(matched_lines)
        else:
            raise UserError(_('No products could be matched. Cannot create quotation.'))
        
        # Update wizard with results
        unmatched_text = ''
        for line in unmatched_lines:
            unmatched_text += f"Line: {line['raw']}\n"
            unmatched_text += f"  Best match: {line['candidate']} (score: {line['score']:.1f})\n\n"
        
        self.write({
            'sale_order_id': order.id,
            'matched_count': len(matched_lines),
            'unmatched_count': len(unmatched_lines),
            'unmatched_text': unmatched_text or 'All lines matched successfully!',
        })
        
        # Return action to open the created quotation
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': order.id,
            'view_mode': 'form',
            'target': 'current',
        }
