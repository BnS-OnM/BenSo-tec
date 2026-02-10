from odoo import models, fields
from odoo.exceptions import UserError
import base64
import io
import logging
import re
import unicodedata

_logger = logging.getLogger(__name__)

# ---- PDF reader (use PyPDF2 because Odoo 19 expects it for other stacks) ----
try:
    import PyPDF2
except Exception:
    PyPDF2 = None

# ---- Fuzzy ----
try:
    from rapidfuzz import fuzz
    HAVE_RF = True
except Exception:
    from difflib import SequenceMatcher
    HAVE_RF = False
    _logger.info('rapidfuzz not available, using difflib for fuzzy matching')

# ---- Helpers ----

def _norm_text(s: str) -> str:
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().strip()
    s = re.sub(r"\s+", ' ', s)
    return s

# korte product-prefixen die we NIET willen wegfilteren, ook al zijn ze 2-3 letters
WHITELIST_PREFIXES = {"vr", "vrc", "vwl", "vih", "vp", "hb"}
SUFFIX_WHITELIST = {"is", "as", "s1"}

# Specifieke Studio-veldnaam waar het merk in staat (Char property)
BRAND_PROP_FIELD = 'product_properties.8b621cac637a8a24'

SERIE_KEYS = ("vwl", "split", "pure", "tower")  # uitbreidbaar indien nodig


class PdfToQuoteWizard(models.TransientModel):
    _name = 'sale.pdf.to.quote.wizard'
    _description = 'PDF to Quote Wizard'

    # Input fields
    pdf_file = fields.Binary(string='PDF File', required=True)
    filename = fields.Char(string='Filename')
    partner_id = fields.Many2one('res.partner', string='Customer', required=True)
    use_pdf_prices = fields.Boolean(string='Use PDF Prices', default=True)
    name_prefix = fields.Char(string='Reference Prefix', default='GPT-001')

    # Merkfilter (optioneel)
    restrict_to_brand = fields.Boolean(
        string='Limit search to brand', default=False,
        help='Restrict search to a brand using the product template field "%s".' % BRAND_PROP_FIELD
    )
    brand_filter = fields.Char(
        string='Brand name',
        help='Value to filter against product_tmpl_id.%s (using ilike). Example: Vaillant' % BRAND_PROP_FIELD
    )

    # Result fields
    sale_order_id = fields.Many2one('sale.order', string='Created Quotation', readonly=True)
    matched_count = fields.Integer(string='Matched Lines', readonly=True)
    unmatched_count = fields.Integer(string='Unmatched Lines', readonly=True)
    unmatched_text = fields.Text(string='Unmatched Items Details', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
    ], default='draft', string='State')

    # ---------------- PDF Extractie & Detectie ----------------

    def _extract_text_from_pdf(self, pdf_bytes):
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
            if not full_text or len(full_text.strip()) < 50:
                raise UserError('PDF bevat geen leesbare tekst (waarschijnlijk scan). Gelieve een tekst-PDF te uploaden.')
            return full_text
        except UserError:
            raise
        except Exception as e:
            _logger.error('Error extracting PDF text: %s', e)
            raise UserError(f'Failed to extract text from PDF: {str(e)}')

    def _detect_pdf_type(self, text):
        """Retourneert 'table' of 'bottom_bar'"""
        text_lower = text.lower()

        has_artikel_nr = 'artikel nr' in text_lower or 'artikelnr' in text_lower
        has_unit_price = 'unit price' in text_lower or 'eenheidsprijs' in text_lower
        table_pattern = re.compile(r'^\*?\d{5,}\s+\d+\s+', re.MULTILINE)
        has_table_pattern = len(table_pattern.findall(text)) > 2

        has_appliances = 'appliances:' in text_lower
        has_controls = 'controls:' in text_lower

        if has_artikel_nr or has_unit_price or has_table_pattern:
            _logger.info('Detected PDF type: table (quotation format)')
            return 'table'
        elif has_appliances or has_controls:
            _logger.info('Detected PDF type: bottom_bar (Appliances/Controls format)')
            return 'bottom_bar'
        else:
            lines = text.split('\n')[:30]
            debug_text = '\n'.join(lines)
            raise UserError(
                'Could not detect PDF format. Please check the file.\n\nFirst 30 lines of extracted text:\n' + debug_text
            )

    # ---------------- Parsers ----------------

    def _parse_eu_number(self, number_str):
        if not number_str:
            return None
        try:
            cleaned = number_str.strip().replace('.', '').replace(',', '.')
            return float(cleaned)
        except (ValueError, AttributeError):
            return None

    def _parse_bottom_bar(self, text):
        items = []
        appliances_match = re.search(r'Appliances:\s*(.+?)(?=Controls:|$)', text, re.DOTALL | re.IGNORECASE)
        controls_match = re.search(r'Controls:\s*(.+?)(?=$)', text, re.DOTALL | re.IGNORECASE)

        sections = []
        if appliances_match:
            sections.append(('Appliances', appliances_match.group(1)))
        if controls_match:
            sections.append(('Controls', controls_match.group(1)))

        for _section_name, section_text in sections:
            parts = re.split(r',[\s\n]+|;\s*', section_text)
            for item_text in [p.strip() for p in parts if p and p.strip()]:
                codes = self._extract_product_codes(item_text)
                items.append({
                    'codes': codes,
                    'desc': item_text,
                    'qty': 1,
                    'unit_price': None,
                })
        _logger.info('Parsed %s items from bottom bar format', len(items))
        return items

    def _extract_product_codes(self, text):
        codes = []
        patterns = [
            r'\bVR\d{2,4}\b',
            r'\bVRC\s*\d{2,4}\b',
            r'\bVWL\s*\d+(?:[./]\d+)?(?:\.\d+)?\s*[A-Z]{1,4}\b',
            r'\bVIH\s*[A-Z]{1,4}\b',
            r'\bVP\s*RW\s*\d+/\d+\s*[A-Z]\b',
        ]
        for pattern in patterns:
            for match in re.findall(pattern, text, re.IGNORECASE):
                normalized = re.sub(r'\s+', '', match)
                codes.append(normalized)
        return codes

    def _parse_table_lines(self, text):
        items = []
        lines = text.split('\n')
        line_pattern = re.compile(r'^(\*?)(\d{5,})\s+(\d+)\s+(.+?)(?:\s+([\d.,]+)\s+([\d.,]+))?$')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = line_pattern.match(line)
            if m:
                _asterisk, code, qty_str, description, unit_price_str, _line_total_str = m.groups()
                code = code.strip()
                try:
                    qty = int(qty_str)
                except ValueError:
                    qty = 1
                unit_price = self._parse_eu_number(unit_price_str) if unit_price_str else None
                items.append({
                    'code': code,
                    'qty': qty,
                    'desc': description.strip(),
                    'unit_price': unit_price,
                })
        _logger.info('Parsed %s items from table format', len(items))
        return items

    # ---------------- Brand domain & helpers ----------------

    def _brand_domain(self, brand_name, for_model='product.product'):
        if not brand_name:
            return []
        T = self.env['product.template']
        prop_fields = [fname for fname in T._fields.keys() if fname.startswith('product_properties.')]
        domain_options = []
        if for_model == 'product.product':
            for fld in prop_fields:
                domain_options.append((f'product_tmpl_id.{fld}', 'ilike', brand_name))
        else:
            for fld in prop_fields:
                domain_options.append((fld, 'ilike', brand_name))
        if not domain_options:
            return [('product_tmpl_id.name' if for_model == 'product.product' else 'name', 'ilike', brand_name)]
        domain = list(domain_options[0])
        for cond in domain_options[1:]:
            domain = ['|'] + domain + list(cond)
        return domain

    def _token_group(self, token):
        return ['|', '|', ('name', 'ilike', token), ('product_tmpl_id.name', 'ilike', token), ('default_code', 'ilike', token)]

    def _domain_or_groups(self, groups):
        if not groups:
            return []
        dom = groups[0]
        for g in groups[1:]:
            dom = ['|'] + dom + g
        return dom

    # ---------------- Hoofdproduct + serie (alleen voor schema-PDF) ----------------

    def _extract_series_tokens(self, text_line: str):
        """Geef set met serie-tokens uit een regel. Voor nu ondersteunen we: vwl, split, pure, tower."""
        tl = _norm_text(text_line)
        tokens = set()
        for key in SERIE_KEYS:
            if re.search(rf'\b{re.escape(key)}\b', tl):
                tokens.add(key)
        return tokens

    def _detect_head_series_from_items(self, items):
        """Zoek in itemregels naar de regel met 'arotherm' en bepaal de serie-tokens."""
        best = set()
        for it in items:
            desc = it.get('desc') or ''
            tl = _norm_text(desc)
            if 'arotherm' in tl:
                ser = self._extract_series_tokens(desc)
                if ser:
                    best |= ser
        return best

    def _search_products_by_series(self, series_tokens, brand_name=None, include_arotherm_for_head=False, limit=5000):
        Product = self.env['product.product']
        serie_groups = [self._token_group(tok) for tok in series_tokens]
        serie_or = self._domain_or_groups(serie_groups)
        if not serie_or:
            return self.env['product.product']
        if include_arotherm_for_head:
            aro_grp = self._token_group('arotherm')
            domain = ['&'] + aro_grp + serie_or
        else:
            domain = serie_or
        if brand_name:
            brand_dom = self._brand_domain(brand_name, for_model='product.product')
            domain = brand_dom + domain
        return Product.with_context(active_test=False).search(domain, limit=limit)

    def _search_head_and_accessories(self, series_tokens, brand_name=None):
        head = self._search_products_by_series(series_tokens, brand_name=brand_name, include_arotherm_for_head=True)
        rest = self._search_products_by_series(series_tokens, brand_name=brand_name, include_arotherm_for_head=False)
        return head | (rest - head)

    # ---------------- Hoofdactie ----------------

    def action_create_quotation(self):
        self.ensure_one()
        if not self.pdf_file:
            raise UserError('Please upload a PDF file.')
        if not self.partner_id:
            raise UserError('Please select a customer.')

        pdf_bytes = base64.b64decode(self.pdf_file)
        text = self._extract_text_from_pdf(pdf_bytes)
        pdf_type = self._detect_pdf_type(text)

        brand_name = None
        if self.restrict_to_brand:
            brand_name = (self.brand_filter or '').strip() or None  # enkel expliciet
            if brand_name:
                _logger.info('Brand restriction active on %s via %s', brand_name, BRAND_PROP_FIELD)
            else:
                _logger.info('Brand restriction enabled but no brand provided; proceeding without brand filter')

        parsed_items = self._parse_table_lines(text) if pdf_type == 'table' else self._parse_bottom_bar(text)
        if not parsed_items:
            raise UserError('No items found in PDF. Please check the file format.')
        _logger.info('Parsed %s items from PDF', len(parsed_items))

        products_dict = {}
        matched_lines = []
        unmatched_items = []

        if pdf_type == 'bottom_bar':
            # --- NIEUW: schema-logica ---
            series = self._detect_head_series_from_items(parsed_items)
            if series:
                _logger.info('Detected head series tokens: %s', ', '.join(sorted(series)))
                found = self._search_head_and_accessories(series, brand_name=brand_name)
                if found:
                    for p in found:
                        if p.id not in products_dict:
                            products_dict[p.id] = {'qty': 1, 'price': None, 'desc': p.display_name or p.name, 'product': p}
                    matched_lines = parsed_items  # we hebben de serie benut; inhoud is geïnterpreteerd
                else:
                    _logger.info('No products found for series=%s (brand=%s)', series, brand_name)
                    unmatched_items.append({'raw': f"Series: {', '.join(sorted(series))}", 'score': 0, 'candidate': '—', 'method': 'series_search_empty'})
            else:
                _logger.info('No head series detected from schema; fallback to per-item matching')
                # VALLBACK: oude matching per item (fuzzy)
                for item in parsed_items:
                    desc = item.get('desc', '')
                    qty = item.get('qty', 1)
                    # zoektokens = families/suffixen + maten
                    tokens = []
                    for t in re.findall(r"[A-Za-z0-9/\.]+", desc or ''):
                        tl = t.lower()
                        if tl in WHITELIST_PREFIXES or tl in SUFFIX_WHITELIST or (len(tl) > 2 and not tl.isdigit()):
                            tokens.append(tl)
                    # simpele union-zoek op tokens
                    Product = self.env['product.product']
                    groups = [['|', '|', ('name', 'ilike', tok), ('product_tmpl_id.name', 'ilike', tok), ('default_code', 'ilike', tok)] for tok in tokens]
                    domain = groups[0] if groups else []
                    for g in groups[1:]:
                        domain = ['|'] + domain + g
                    if brand_name:
                        domain = self._brand_domain(brand_name, for_model='product.product') + domain
                    cands = Product.with_context(active_test=False).search(domain or [], limit=300)
                    if cands:
                        p = cands[0]
                        products_dict.setdefault(p.id, {'qty': 0, 'price': None, 'desc': p.display_name or p.name, 'product': p})
                        products_dict[p.id]['qty'] += qty
                        matched_lines.append(item)
                    else:
                        unmatched_items.append({'raw': f"Desc: {desc}", 'score': 0, 'candidate': '—', 'method': 'fallback_schema'})
        else:
            # "table" modus: ongewijzigd – probeer code en fuzzy per item
            for item in parsed_items:
                code = item.get('code')
                desc = item.get('desc', '')
                qty = item.get('qty', 1)
                unit_price = item.get('unit_price')
                Product = self.env['product.product']
                p = Product.with_context(active_test=False).search([('default_code', '=', code)], limit=1) if code else False
                if not p and desc:
                    # simpele fuzzy: pak beste ilike kandidaat
                    groups = [['|', '|', ('name', 'ilike', w), ('product_tmpl_id.name', 'ilike', w), ('default_code', 'ilike', w)] for w in re.findall(r"\w+", desc) if len(w) > 2][:4]
                    domain = groups[0] if groups else []
                    for g in groups[1:]:
                        domain = ['|'] + domain + g
                    if brand_name:
                        domain = self._brand_domain(brand_name, for_model='product.product') + domain
                    cands = Product.with_context(active_test=False).search(domain or [], limit=200)
                    p = cands[:1]
                if p:
                    p = p[0]
                    products_dict.setdefault(p.id, {'qty': 0, 'price': None, 'desc': p.display_name or p.name, 'product': p})
                    products_dict[p.id]['qty'] += qty
                    if unit_price and self.use_pdf_prices:
                        products_dict[p.id]['price'] = unit_price
                    matched_lines.append(item)
                else:
                    unmatched_items.append({'raw': f"Code: {code}, Desc: {desc}, Qty: {qty}", 'score': 0, 'candidate': '—', 'method': 'table'})

        # Create sale order
        order_vals = {
            'partner_id': self.partner_id.id,
            'origin': f"{self.name_prefix} - {self.filename or 'PDF Import'}",
            'client_order_ref': f"{self.name_prefix} - {self.filename or 'PDF Import'}",
        }
        sale_order = self.env['sale.order'].create(order_vals)

        # Create order lines
        for _product_id, line_data in products_dict.items():
            product = line_data['product']
            line_vals = {
                'order_id': sale_order.id,
                'product_id': product.id,
                'product_uom_qty': line_data['qty'] or 1,
                'name': (product.get_product_multiline_description_sale() if hasattr(product, 'get_product_multiline_description_sale') else (product.display_name or product.name)),
            }
            if line_data.get('price') is not None:
                line_vals['price_unit'] = line_data['price']
            self.env['sale.order.line'].create(line_vals)

        unmatched_text = ''
        if unmatched_items:
            chunks = []
            if brand_name:
                chunks.append(f"Brand filter via {BRAND_PROP_FIELD}: {brand_name}")
            for item in unmatched_items:
                chunks.append(f"• {item['raw']}\n  Method: {item['method']}")
            unmatched_text = '\n'.join(chunks)

        self.write({
            'sale_order_id': sale_order.id,
            'matched_count': len(matched_lines),
            'unmatched_count': len(unmatched_items),
            'unmatched_text': unmatched_text,
            'state': 'done',
        })

        _logger.info('Created sale order %s: %s matched, %s unmatched (brand=%s)', sale_order.name, len(matched_lines), len(unmatched_items), brand_name)

        return {
            'type': 'ir.actions.act_window',
            'name': 'Created Quotation',
            'res_model': 'sale.order',
            'res_id': sale_order.id,
            'view_mode': 'form',
            'target': 'current',
        }
