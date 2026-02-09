from odoo import models, fields, api
from odoo.exceptions import UserError
import base64
import io
import logging
import re

_logger = logging.getLogger(__name__)

# Try to import PyPDF2
try:
    import PyPDF2
except ImportError:
    _logger.warning('PyPDF2 library is not installed. PDF parsing will not work.')
    PyPDF2 = None

# Try to import rapidfuzz, fallback to difflib
try:
    from rapidfuzz import fuzz
    FUZZY_MATCHER = 'rapidfuzz'
except ImportError:
    from difflib import SequenceMatcher
    FUZZY_MATCHER = 'difflib'
    _logger.info('rapidfuzz not available, using difflib for fuzzy matching')


class PdfToQuoteWizard(models.TransientModel):
    _name = 'sale.pdf.to.quote.wizard'
    _description = 'PDF to Quote Wizard'

    # Input fields
    pdf_file = fields.Binary(string='PDF File', required=True)
    filename = fields.Char(string='Filename')
    partner_id = fields.Many2one('res.partner', string='Customer', required=True,
                                  help='Select the customer for this quotation')
    use_pdf_prices = fields.Boolean(string='Use PDF Prices', default=True,
                                      help='If enabled and prices are present in PDF, use them. '
                                           'Otherwise let Odoo determine prices from pricelist.')
    name_prefix = fields.Char(string='Reference Prefix', default='GPT-001',
                              help='Prefix for traceability in origin/client_order_ref')
    
    # Result fields (readonly, shown after processing)
    sale_order_id = fields.Many2one('sale.order', string='Created Quotation', readonly=True)
    matched_count = fields.Integer(string='Matched Lines', readonly=True)
    unmatched_count = fields.Integer(string='Unmatched Lines', readonly=True)
    unmatched_text = fields.Text(string='Unmatched Items Details', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
    ], default='draft', string='State')

    def _extract_text_from_pdf(self, pdf_bytes):
        """Extract text from PDF using PyPDF2
        
        Args:
            pdf_bytes: Binary PDF data
            
        Returns:
            str: Extracted text from all pages
            
        Raises:
            UserError: If PyPDF2 is not installed or PDF has no readable text
        """
        if not PyPDF2:
            raise UserError('PyPDF2 library is not installed. Please install it using: pip install PyPDF2')
        
        try:
            pdf_file_obj = io.BytesIO(pdf_bytes)
            pdf_reader = PyPDF2.PdfReader(pdf_file_obj)
            
            text_content = []
            for page in pdf_reader.pages:
                text = page.extract_text()
                if text:
                    text_content.append(text)
            
            full_text = '\n'.join(text_content)
            
            # Check if text is empty or too short (likely a scanned PDF)
            if not full_text or len(full_text.strip()) < 50:
                raise UserError(
                    'PDF bevat geen leesbare tekst (waarschijnlijk scan). '
                    'Gelieve een tekst-PDF te uploaden.'
                )
            
            return full_text
        except UserError:
            raise
        except Exception as e:
            _logger.error(f'Error extracting PDF text: {str(e)}')
            raise UserError(f'Failed to extract text from PDF: {str(e)}')

    def _detect_pdf_type(self, text):
        """Detect PDF type based on content patterns
        
        Args:
            text: Extracted PDF text
            
        Returns:
            str: 'table' or 'bottom_bar'
            
        Raises:
            UserError: If no known pattern is detected
        """
        text_lower = text.lower()
        
        # Check for table format indicators
        has_artikel_nr = 'artikel nr' in text_lower or 'artikelnr' in text_lower
        has_unit_price = 'unit price' in text_lower or 'eenheidsprijs' in text_lower
        
        # Check for table pattern: article number (5+ digits) followed by quantity
        table_pattern = re.compile(r'^\*?\d{5,}\s+\d+\s+', re.MULTILINE)
        has_table_pattern = len(table_pattern.findall(text)) > 2
        
        # Check for bottom bar pattern
        has_appliances = 'Appliances:' in text
        has_controls = 'Controls:' in text
        
        # Preference: table > bottom_bar
        if has_artikel_nr or has_unit_price or has_table_pattern:
            _logger.info('Detected PDF type: table (quotation format)')
            return 'table'
        elif has_appliances or has_controls:
            _logger.info('Detected PDF type: bottom_bar (Appliances/Controls format)')
            return 'bottom_bar'
        else:
            # Show first 30 lines for debugging
            lines = text.split('\n')[:30]
            debug_text = '\n'.join(lines)
            raise UserError(
                f'Could not detect PDF format. Please check the file.\n\n'
                f'First 30 lines of extracted text:\n{debug_text}'
            )

    def _parse_eu_number(self, number_str):
        """Parse European number format (1.234,56) to float
        
        Args:
            number_str: Number string in European format
            
        Returns:
            float or None: Parsed number or None if parsing fails
        """
        if not number_str:
            return None
        
        try:
            # Remove spaces and convert European format to standard
            cleaned = number_str.strip().replace('.', '').replace(',', '.')
            return float(cleaned)
        except (ValueError, AttributeError):
            return None

    def _parse_bottom_bar(self, text):
        """Parse PDF with bottom bar schema (Appliances:/Controls:)
        
        Args:
            text: Extracted PDF text
            
        Returns:
            list: List of dicts with keys: codes, desc, qty
        """
        items = []
        
        # Find Appliances and Controls sections
        appliances_match = re.search(r'Appliances:\s*(.+?)(?=Controls:|$)', text, re.DOTALL | re.IGNORECASE)
        controls_match = re.search(r'Controls:\s*(.+?)(?=$)', text, re.DOTALL | re.IGNORECASE)
        
        sections = []
        if appliances_match:
            sections.append(('Appliances', appliances_match.group(1)))
        if controls_match:
            sections.append(('Controls', controls_match.group(1)))
        
        for section_name, section_text in sections:
            # Split by comma and process each item
            item_parts = [item.strip() for item in section_text.split(',') if item.strip()]
            
            for item_text in item_parts:
                # Try to extract product codes from the item text
                codes = self._extract_product_codes(item_text)
                
                items.append({
                    'codes': codes,
                    'desc': item_text,
                    'qty': 1,  # Default quantity
                    'unit_price': None,  # No prices in bottom bar type
                })
        
        _logger.info(f'Parsed {len(items)} items from bottom bar format')
        return items

    def _extract_product_codes(self, text):
        """Extract product codes from text using regex patterns
        
        Patterns:
        - VR\d{2,4} (e.g., VR71, VR940)
        - VRC\s*\d{2,4} (e.g., VRC720, VRC 720)
        - VWL\s*\d+(\.\d+)?\s*[A-Z]{1,4} (e.g., VWL 8.2 AS, VWL 8.2 IS)
        - VIH\s*[A-Z]{1,4} (e.g., VIH RW)
        - VP\sRW\s\d+/\d+\s*[A-Z] (e.g., VP RW 45/2 B)
        
        Args:
            text: Text to search for codes
            
        Returns:
            list: List of found codes (normalized)
        """
        codes = []
        
        patterns = [
            r'\bVR\d{2,4}\b',
            r'\bVRC\s*\d{2,4}\b',
            r'\bVWL\s*\d+(?:\.\d+)?\s*[A-Z]{1,4}\b',
            r'\bVIH\s*[A-Z]{1,4}\b',
            r'\bVP\sRW\s\d+/\d+\s*[A-Z]\b',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                # Normalize: remove extra spaces
                normalized = re.sub(r'\s+', '', match)
                codes.append(normalized)
        
        return codes

    def _parse_table_lines(self, text):
        """Parse PDF with table/quotation format
        
        Expected format:
        article_number quantity description [unit_price] [line_total]
        
        Examples:
        956395 1 AROTHERM SPLIT PLUS ... 5.268,00 5.268,00
        *208070 1 SCHROEFCILINDER M8 0,50 0,50
        
        Args:
            text: Extracted PDF text
            
        Returns:
            list: List of dicts with keys: code, qty, desc, unit_price
        """
        items = []
        lines = text.split('\n')
        
        # Pattern: optional *, then 5+ digits (article number), then space, then digit (qty)
        line_pattern = re.compile(
            r'^(\*?)(\d{5,})\s+(\d+)\s+(.+?)(?:\s+([\d.,]+)\s+([\d.,]+))?$'
        )
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            match = line_pattern.match(line)
            if match:
                asterisk, code, qty_str, description, unit_price_str, line_total_str = match.groups()
                
                # Remove asterisk from code if present
                code = code.strip()
                
                # Parse quantity
                try:
                    qty = int(qty_str)
                except ValueError:
                    qty = 1
                
                # Parse unit price (European format)
                unit_price = self._parse_eu_number(unit_price_str) if unit_price_str else None
                
                items.append({
                    'code': code,
                    'qty': qty,
                    'desc': description.strip(),
                    'unit_price': unit_price,
                })
        
        _logger.info(f'Parsed {len(items)} items from table format')
        return items

    def _fuzzy_match_score(self, str1, str2):
        """Calculate fuzzy match score between two strings
        
        Args:
            str1, str2: Strings to compare
            
        Returns:
            float: Match score (0-100)
        """
        if FUZZY_MATCHER == 'rapidfuzz':
            return fuzz.ratio(str1.lower(), str2.lower())
        else:
            # Using difflib
            return SequenceMatcher(None, str1.lower(), str2.lower()).ratio() * 100

    def _match_product(self, code=None, desc=None):
        """Match a product by code or description
        
        Args:
            code: Product code (default_code) - can be string or list of codes
            desc: Product description for fuzzy matching
            
        Returns:
            tuple: (product_record, score, method, candidate_name)
                   or (None, 0, None, None) if no match
        """
        Product = self.env['product.product']
        
        # Try matching by code first (exact match)
        if code:
            codes_to_try = code if isinstance(code, list) else [code]
            
            for single_code in codes_to_try:
                # Try exact match on product.product
                product = Product.search([('default_code', '=', single_code)], limit=1)
                if product:
                    _logger.debug(f'Matched by code {single_code}: {product.display_name}')
                    return (product, 100, 'code_exact', product.display_name)
                
                # Try exact match on product.template
                template = self.env['product.template'].search([('default_code', '=', single_code)], limit=1)
                if template:
                    # Get first variant
                    product = Product.search([('product_tmpl_id', '=', template.id)], limit=1)
                    if product:
                        _logger.debug(f'Matched by template code {single_code}: {product.display_name}')
                        return (product, 100, 'code_template', product.display_name)
        
        # Fallback to fuzzy matching on description
        if desc and len(desc) > 3:
            # Extract 2-4 significant tokens for domain filter
            tokens = [t for t in re.findall(r'\w+', desc.lower()) if len(t) > 2][:4]
            
            if tokens:
                # Build domain with ilike on tokens for performance
                domain = ['|'] * (len(tokens) - 1) if len(tokens) > 1 else []
                for token in tokens:
                    domain.append(('name', 'ilike', token))
                
                candidates = Product.search(domain, limit=100)
                
                best_score = 0
                best_product = None
                best_name = None
                
                for product in candidates:
                    score = self._fuzzy_match_score(desc, product.display_name)
                    if score > best_score:
                        best_score = score
                        best_product = product
                        best_name = product.display_name
                
                if best_score >= 80:
                    _logger.debug(f'Matched by fuzzy ({best_score}%): {best_name}')
                    return (best_product, best_score, 'fuzzy', best_name)
                elif best_product:
                    _logger.debug(f'Best fuzzy match ({best_score}%) below threshold: {best_name}')
                    return (None, best_score, 'fuzzy_failed', best_name)
        
        return (None, 0, None, None)

    def action_create_quotation(self):
        """Main action: parse PDF and create sale order"""
        self.ensure_one()
        
        if not self.pdf_file:
            raise UserError('Please upload a PDF file.')
        
        if not self.partner_id:
            raise UserError('Please select a customer.')
        
        # Extract text from PDF
        pdf_bytes = base64.b64decode(self.pdf_file)
        text = self._extract_text_from_pdf(pdf_bytes)
        
        # Detect PDF type
        pdf_type = self._detect_pdf_type(text)
        
        # Parse PDF based on type
        if pdf_type == 'table':
            parsed_items = self._parse_table_lines(text)
        else:  # bottom_bar
            parsed_items = self._parse_bottom_bar(text)
        
        if not parsed_items:
            raise UserError('No items found in PDF. Please check the file format.')
        
        _logger.info(f'Parsed {len(parsed_items)} items from PDF')
        
        # Match products and prepare order lines
        matched_lines = []
        unmatched_items = []
        products_dict = {}  # key: product_id, value: {'qty': qty, 'price': price, 'desc': desc}
        
        for item in parsed_items:
            if pdf_type == 'table':
                code = item.get('code')
                desc = item.get('desc', '')
                qty = item.get('qty', 1)
                unit_price = item.get('unit_price')
            else:  # bottom_bar
                code = item.get('codes')  # list of codes
                desc = item.get('desc', '')
                qty = item.get('qty', 1)
                unit_price = item.get('unit_price')
            
            # Match product
            product, score, method, candidate_name = self._match_product(code=code, desc=desc)
            
            if product:
                # Combine duplicate products
                if product.id in products_dict:
                    products_dict[product.id]['qty'] += qty
                    _logger.debug(f'Combined duplicate product {product.display_name}, new qty: {products_dict[product.id]["qty"]}')
                else:
                    products_dict[product.id] = {
                        'qty': qty,
                        'price': unit_price if (unit_price and self.use_pdf_prices) else None,
                        'desc': desc if desc else product.display_name,
                        'product': product,
                    }
                matched_lines.append(item)
            else:
                unmatched_items.append({
                    'raw': f"Code: {code}, Desc: {desc}, Qty: {qty}",
                    'score': score,
                    'candidate': candidate_name or 'No candidate found',
                    'method': method or 'no_match',
                })
        
        # Create sale order
        order_vals = {
            'partner_id': self.partner_id.id,
            'origin': f"{self.name_prefix} - {self.filename or 'PDF Import'}",
            'client_order_ref': f"{self.name_prefix} - {self.filename or 'PDF Import'}",
        }
        
        sale_order = self.env['sale.order'].create(order_vals)
        
        # Create order lines
        for product_id, line_data in products_dict.items():
            product = line_data['product']
            line_vals = {
                'order_id': sale_order.id,
                'product_id': product.id,
                'product_uom_qty': line_data['qty'],
                'product_uom': product.uom_id.id,
                'name': line_data['desc'],
            }
            
            # Set price if available and use_pdf_prices is True
            if line_data['price'] is not None:
                line_vals['price_unit'] = line_data['price']
            
            self.env['sale.order.line'].create(line_vals)
        
        # Prepare unmatched text
        unmatched_text = ''
        if unmatched_items:
            unmatched_lines = []
            for item in unmatched_items:
                unmatched_lines.append(
                    f"• {item['raw']}\n"
                    f"  Best candidate: {item['candidate']} (score: {item['score']:.1f}%)\n"
                )
            unmatched_text = '\n'.join(unmatched_lines)
        
        # Update wizard with results
        self.write({
            'sale_order_id': sale_order.id,
            'matched_count': len(matched_lines),
            'unmatched_count': len(unmatched_items),
            'unmatched_text': unmatched_text,
            'state': 'done',
        })
        
        _logger.info(
            f'Created sale order {sale_order.name}: '
            f'{len(matched_lines)} matched, {len(unmatched_items)} unmatched'
        )
        
        # Return action to open the created sale order
        return {
            'type': 'ir.actions.act_window',
            'name': 'Created Quotation',
            'res_model': 'sale.order',
            'res_id': sale_order.id,
            'view_mode': 'form',
            'target': 'current',
        }
