from odoo import models, fields
from odoo.exceptions import UserError
import base64
import io
import logging
import re
import unicodedata

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
    HAVE_RF = True
except Exception:
    from difflib import SequenceMatcher
    HAVE_RF = False
    _logger.info('rapidfuzz not available, using difflib for fuzzy matching')

# ---------------- Normalisatie helpers ----------------

def _norm_text(s: str) -> str:
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().strip()
    s = re.sub(r"\s+", ' ', s)
    return s

def _clean_desc_for_match(desc: str) -> str:
    if not desc:
        return ''
    # verwijder losse prijzen/getallen op het einde (vb. "..., 5.268,00")
    out = re.sub(r"[\s\-–—]*[\d\.,]+\s*€?$", '', desc.strip())
    return out

def _clean_code(c: str) -> str:
    if not c:
        return ''
    # verwijder spaties, puntjes, streepjes; uppercase
    return re.sub(r"[\s\-.]", '', c).upper()

# korte product-prefixen die we NIET willen wegfilteren, ook al zijn ze 2-3 letters
WHITELIST_PREFIXES = {"vr", "vrc", "vwl", "vih", "vp", "hb"}
WHITELIST_SUFFIXES = {"is", "as", "s1"}

# Specifieke Studio-veldnaam waar het merk in staat (Char property)
BRAND_PROP_FIELD = 'product_properties.8b621cac637a8a24'

FAMILY_TOKENS = ("vwl", "vrc", "vr", "vih", "vp")


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
            # split per komma en puntkomma
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
            # VWL varianten met punt of slash in maat: "VWL 8.2 IS", "VWL 75/8.2 AS"
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
        """Generic brand filter over **any** Studio product properties on product.template.
        - Enumerates all fields on product.template starting with 'product_properties.'
        - Builds an OR-domain across those fields using ilike brand_name
        - Works for both product.product (via product_tmpl_id.<prop>) and product.template (<prop>)
        """
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
            if for_model == 'product.product':
                domain_options.append(('name', 'ilike', brand_name))
                domain_options.append(('product_tmpl_id.name', 'ilike', brand_name))
            else:
                domain_options.append(('name', 'ilike', brand_name))
        domain = domain_options[0]
        for cond in domain_options[1:]:
            domain = ['|'] + list(domain) + list(cond)
        return domain

    PREFIX_BRAND_MAP = {
        'VRC': 'Vaillant',
        'VWL': 'Vaillant',
        'VR': 'Vaillant',
        'VIH': 'Vaillant',
        'VP': 'Vaillant',
    }

    def _auto_detect_brand(self, text):
        tl = text.lower()
        for prefix, brand in self.PREFIX_BRAND_MAP.items():
            if re.search(r'\b' + re.escape(prefix.lower()) + r'\b', tl):
                return brand
        return None

    # ---------------- Matching ----------------

    def _fuzzy_match_score(self, str1, str2):
        a = _norm_text(str1)
        b = _norm_text(str2)
        if HAVE_RF:
            return fuzz.token_set_ratio(a, b)
        else:
            return SequenceMatcher(None, a, b).ratio() * 100

    def _search_candidates(self, tokens, langs=("nl_BE", "en_US"), code_tokens=None, brand_name=None):
        """Voer kleinere per-token queries uit en unieer de resultaten.
        Dit vermijdt een gigantische OR-domain (performanter en minder kans op timeouts)."""
        Product = self.env['product.product']
        candidates = self.env['product.product']

        brand_dom_product = self._brand_domain(brand_name, for_model='product.product') if brand_name else []
        tok_list = list(tokens or [])[:8]
        code_tok_list = list(code_tokens or [])[:4]

        def token_domain(tok):
            return ['|', '|', ('name', 'ilike', tok), ('product_tmpl_id.name', 'ilike', tok), ('default_code', 'ilike', tok)]

        per_token_limit = 200
        for lang in langs:
            ctx = {'lang': lang, 'active_test': False}
            for ct in code_tok_list:
                dom = (brand_dom_product + token_domain(ct)) if brand_dom_product else token_domain(ct)
                res = Product.with_context(**ctx).search(dom, limit=per_token_limit)
                candidates |= res
            for t in tok_list:
                dom = (brand_dom_product + token_domain(t)) if brand_dom_product else token_domain(t)
                res = Product.with_context(**ctx).search(dom, limit=per_token_limit)
                candidates |= res
        _logger.debug('Candidates (union) found: %s (brand=%s)', len(candidates), brand_name)
        return candidates

    def _match_by_code(self, code, brand_name=None):
        Product = self.env['product.product']
        Template = self.env['product.template']
        ctx = {'active_test': False}
        if not code:
            return None
        codes_to_try = code if isinstance(code, list) else [code]
        for c in codes_to_try:
            c_norm = _clean_code(c)
            dom = [('default_code', '=', c)]
            if brand_name:
                dom = self._brand_domain(brand_name, for_model='product.product') + dom
            p = Product.with_context(**ctx).search(dom, limit=1)
            if p:
                return p
            dom = [('default_code', 'ilike', c.replace(' ', '').replace('-', ''))]
            if brand_name:
                dom = self._brand_domain(brand_name, for_model='product.product') + dom
            cand = Product.with_context(**ctx).search(dom, limit=200)
            for x in cand:
                if _clean_code(x.default_code or '') == c_norm:
                    return x
            t_dom = [('default_code', '=', c)]
            if brand_name:
                t_dom = self._brand_domain(brand_name, for_model='product.template') + t_dom
            t = Template.with_context(**ctx).search(t_dom, limit=1)
            if t:
                p = Product.with_context(**ctx).search([('product_tmpl_id', '=', t.id)], limit=1)
                if p:
                    return p
        return None

    def _build_tokens(self, desc: str, codes_from_desc):
        raw = re.findall(r"[A-Za-z0-9/\.]+", desc or '')
        tokens = []
        for t in raw:
            tl = t.lower()
            if tl in WHITELIST_PREFIXES:
                tokens.append(tl)
                continue
            if tl in WHITELIST_SUFFIXES or (len(tl) > 2 and not tl.isdigit()):
                tokens.append(tl)
        for c in (codes_from_desc or []):
            if c:
                tokens.append(_clean_code(c))
        if not tokens and raw:
            raw_sorted = sorted(set(raw), key=len, reverse=True)
            tokens = [w.lower() for w in raw_sorted[:2]]
        seen = set(); ordered = []
        for t in tokens:
            if t not in seen:
                ordered.append(t); seen.add(t)
        return ordered[:10]

    def _rank_candidates(self, desc, candidates):
        if not candidates:
            return []
        desc_clean = _clean_desc_for_match(desc)
        fams = [f for f in FAMILY_TOKENS if re.search(rf'\b{re.escape(f)}\b', _norm_text(desc))]
        size_match = re.search(r"\b\d{1,3}(?:[./]\d{1,2})?(?:\.\d{1,2})?\b", _norm_text(desc))
        size = size_match.group(0) if size_match else None
        scored = []
        for p in candidates:
            label = p.display_name or p.name or ''
            s = self._fuzzy_match_score(desc_clean, label)
            label_n = _norm_text(label)
            for f in fams:
                if re.search(rf'\b{re.escape(f)}\b', label_n):
                    s += 5; break
            if size and size.replace(',', '.') in label_n:
                s += 5
            scored.append((s, p, label))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:3]

    def _match_product(self, code=None, desc=None, brand_name=None, schema_mode=False):
        product = self._match_by_code(code, brand_name=brand_name)
        if product:
            _logger.debug('Matched by code (brand=%s): %s', brand_name, product.display_name)
            return (product, 100, 'code_exact', product.display_name)

        if desc and len(desc) > 3:
            codes_from_desc = self._extract_product_codes(desc)
            tokens = self._build_tokens(desc, codes_from_desc)

            # Schema-modus: familie OF maat is voldoende (voorheen beide). Voeg maat-varianten toe.
            if schema_mode:
                text = _norm_text(desc)
                has_family = any(tok in tokens for tok in FAMILY_TOKENS)
                size_match = re.search(r"\b\d{1,3}(?:[./]\d{1,2})?(?:\.\d{1,2})?\b", text)
                if not (has_family or size_match):
                    return (None, 0, 'schema_too_vague', None)
                fam = next((t for t in tokens if t in FAMILY_TOKENS), None)
                if fam:
                    tokens = [t for t in tokens if t != fam]
                    tokens.insert(0, fam)
                if size_match:
                    size_tok = size_match.group(0)
                    size_dot = size_tok.replace(',', '.')
                    size_com = size_dot.replace('.', ',')
                    for v in {size_tok, size_dot, size_com}:
                        if v not in tokens:
                            tokens.insert(0, v)
                    m = re.match(r"^(\d{1,3})[\/ ](\d{1,2})(?:[.,](\d{1,2}))?$", size_dot)
                    if m:
                        A, B, C = m.group(1), m.group(2), m.group(3)
                        if C:
                            for v in {f"{A}{B}{C}", f"{A} {B}.{C}", f"{A} {B},{C}"}:
                                if v not in tokens:
                                    tokens.append(v)
                    else:
                        m2 = re.match(r"^(\d{1,2})[.,](\d{1,2})$", size_dot)
                        if m2:
                            X, Y = m2.group(1), m2.group(2)
                            flat = f"{X}{Y}"
                            if flat not in tokens:
                                tokens.append(flat)

            code_tokens_norm = [re.sub(r"\s+", "", c) for c in (codes_from_desc or [])]
            candidates = self._search_candidates(tokens, code_tokens=code_tokens_norm, brand_name=brand_name)

            # code in display_name/name hard-match
            if codes_from_desc:
                for p in candidates:
                    label = (p.display_name or p.name or '')
                    label_norm = re.sub(r"[\s\-\.\/]", "", label).upper()
                    for c in codes_from_desc:
                        c_norm = re.sub(r"[\s\/]", "", c).upper()
                        if c_norm and c_norm in label_norm:
                            _logger.debug('Matched by code-in-name (brand=%s): %s contains %s', brand_name, label, c)
                            return (p, 96, 'code_in_name', label)

            top3 = self._rank_candidates(desc, candidates)
            if top3:
                best_score, best_product, best_name = top3[0]
                cutoff = (74 if schema_mode else 75) if HAVE_RF else (66 if schema_mode else 68)
                if best_score >= cutoff:
                    _logger.debug('Matched by fuzzy (%s%%) (brand=%s): %s', best_score, brand_name, best_name)
                    return (best_product, best_score, 'fuzzy', best_name)
                else:
                    return (None, best_score, 'fuzzy_failed', best_name)

        return (None, 0, None, None)

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
            brand_name = (self.brand_filter or '').strip() or self._auto_detect_brand(text)
            if brand_name:
                _logger.info('Brand restriction active on %s via %s', brand_name, BRAND_PROP_FIELD)
            else:
                _logger.info('Brand restriction enabled but no brand detected; proceeding without brand filter')

        parsed_items = self._parse_table_lines(text) if pdf_type == 'table' else self._parse_bottom_bar(text)
        if not parsed_items:
            raise UserError('No items found in PDF. Please check the file format.')

        _logger.info('Parsed %s items from PDF', len(parsed_items))

        matched_lines = []
        unmatched_items = []
        products_dict = {}

        for item in parsed_items:
            if pdf_type == 'table':
                code = item.get('code')
                desc = item.get('desc', '')
                qty = item.get('qty', 1)
                unit_price = item.get('unit_price')
            else:
                code = item.get('codes')
                desc = item.get('desc', '')
                qty = item.get('qty', 1)
                unit_price = item.get('unit_price')

            product, score, method, candidate_name = self._match_product(
                code=code,
                desc=desc,
                brand_name=brand_name,
                schema_mode=(pdf_type == 'bottom_bar'),
            )

            if product:
                if product.id in products_dict:
                    products_dict[product.id]['qty'] += qty
                    _logger.debug('Combined duplicate product %s, new qty: %s', product.display_name, products_dict[product.id]['qty'])
                else:
                    products_dict[product.id] = {
                        'qty': qty,
                        'price': unit_price if (unit_price and self.use_pdf_prices) else None,
                        'desc': desc if desc else (product.display_name or product.name),
                        'product': product,
                    }
                matched_lines.append(item)
            else:
                # toon wat diagnose info (top3)
                cand = self._search_candidates(self._build_tokens(desc or '', self._extract_product_codes(desc or '')),
                                               code_tokens=None, brand_name=brand_name)
                tmp_top3 = self._rank_candidates(desc or '', cand)[:3]
                top3_txt = " / ".join([f"{int(s)}%: {n}" for s, _p, n in tmp_top3]) if tmp_top3 else '—'
                unmatched_items.append({
                    'raw': f"Code: {code}, Desc: {desc}, Qty: {qty}",
                    'score': score,
                    'candidate': candidate_name or 'No candidate found',
                    'method': method or 'no_match',
                    'top3_txt': top3_txt,
                })

        order_vals = {
            'partner_id': self.partner_id.id,
            'origin': f"{self.name_prefix} - {self.filename or 'PDF Import'}",
            'client_order_ref': f"{self.name_prefix} - {self.filename or 'PDF Import'}",
        }
        sale_order = self.env['sale.order'].create(order_vals)

        for _product_id, line_data in products_dict.items():
            product = line_data['product']
            line_vals = {
                'order_id': sale_order.id,
                'product_id': product.id,
                'product_uom_qty': line_data['qty'],
                'name': line_data['desc'],
            }
            if line_data['price'] is not None:
                line_vals['price_unit'] = line_data['price']
            self.env['sale.order.line'].create(line_vals)

        unmatched_text = ''
        if unmatched_items:
            chunks = []
            if brand_name:
                chunks.append(f"Brand filter via {BRAND_PROP_FIELD}: {brand_name}")
            for item in unmatched_items:
                chunks.append(
                    f"• {item['raw']}\n  Best candidate: {item['candidate']} (score: {item['score']:.1f}%)\n  Top3: {item['top3_txt']}"
                )
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
