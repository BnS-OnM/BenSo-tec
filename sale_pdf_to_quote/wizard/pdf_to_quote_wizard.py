from odoo import models, fields
from odoo.exceptions import UserError
import base64
import io
import logging
import re
import unicodedata

_logger = logging.getLogger(__name__)

# Prefer pypdf, fallback to PyPDF2 (<3 expected by Odoo). This wizard only reads PDFs; Odoo's report stack is separate.
try:
    from pypdf import PdfReader as _PdfReader
except Exception:
    try:
        from PyPDF2 import PdfReader as _PdfReader
    except Exception:
        _PdfReader = None

# Fuzzy
try:
    from rapidfuzz import fuzz
    HAVE_RF = True
except Exception:
    from difflib import SequenceMatcher
    HAVE_RF = False
    _logger.info('rapidfuzz not available, using difflib for fuzzy matching')

# --- Helpers ---

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
    return re.sub(r"[\s\-–—]*[\d\.,]+\s*€?$", '', desc.strip())

def _clean_code(c: str) -> str:
    if not c:
        return ''
    return re.sub(r"[\s\-\.]", '', c).upper()

WHITELIST_PREFIXES = {"vr", "vrc", "vwl", "vih", "vp", "hb"}
SUFFIX_WHITELIST = {"is", "as", "s1"}
FAMILY_TOKENS = ("vwl", "vrc", "vr", "vih", "vp")

BRAND_PROP_FIELD = 'product_properties.8b621cac637a8a24'


class PdfToQuoteWizard(models.TransientModel):
    _name = 'sale.pdf.to.quote.wizard'
    _description = 'PDF to Quote Wizard'

    pdf_file = fields.Binary(string='PDF File', required=True)
    filename = fields.Char(string='Filename')
    partner_id = fields.Many2one('res.partner', string='Customer', required=True)
    use_pdf_prices = fields.Boolean(string='Use PDF Prices', default=True)
    name_prefix = fields.Char(string='Reference Prefix', default='GPT-001')

    # Optioneel, maar standaard buiten gebruik om lockrisico te beperken
    restrict_to_brand = fields.Boolean(string='Limit search to brand', default=False)
    brand_filter = fields.Char(string='Brand name')

    sale_order_id = fields.Many2one('sale.order', string='Created Quotation', readonly=True)
    matched_count = fields.Integer(string='Matched Lines', readonly=True)
    unmatched_count = fields.Integer(string='Unmatched Lines', readonly=True)
    unmatched_text = fields.Text(string='Unmatched Items Details', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
    ], default='draft', string='State')

    # --- PDF ---
    def _extract_text_from_pdf(self, pdf_bytes):
        if not _PdfReader:
            raise UserError("Geen PDF-lezer beschikbaar. Installeer 'pypdf' of pin 'PyPDF2<3'.")
        try:
            reader = _PdfReader(io.BytesIO(pdf_bytes))
            text_content = []
            for page in getattr(reader, 'pages', []) or []:
                try:
                    t = page.extract_text()
                except Exception:
                    t = ''
                if t:
                    text_content.append(t)
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
        tl = text.lower()
        has_artikel_nr = 'artikel nr' in tl or 'artikelnr' in tl
        has_unit_price = 'unit price' in tl or 'eenheidsprijs' in tl
        table_pattern = re.compile(r'^\*?\d{5,}\s+\d+\s+', re.MULTILINE)
        has_table_pattern = len(table_pattern.findall(text)) > 2
        has_appliances = 'appliances:' in tl
        has_controls = 'controls:' in tl
        if has_artikel_nr or has_unit_price or has_table_pattern:
            _logger.info('Detected PDF type: table (quotation format)')
            return 'table'
        if has_appliances or has_controls:
            _logger.info('Detected PDF type: bottom_bar (Appliances/Controls format)')
            return 'bottom_bar'
        lines = text.split('\n')[:30]
        raise UserError('Could not detect PDF format. First lines:\n' + '\n'.join(lines))

    # --- Parsers ---
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
            sections.append(appliances_match.group(1))
        if controls_match:
            sections.append(controls_match.group(1))
        for section_text in sections:
            parts = re.split(r',[\s\n]+|;\s*', section_text)
            for item_text in [p.strip() for p in parts if p and p.strip()]:
                codes = self._extract_product_codes(item_text)
                items.append({'codes': codes, 'desc': item_text, 'qty': 1, 'unit_price': None})
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
                try:
                    qty = int(qty_str)
                except ValueError:
                    qty = 1
                unit_price = self._parse_eu_number(unit_price_str) if unit_price_str else None
                items.append({'code': code.strip(), 'qty': qty, 'desc': description.strip(), 'unit_price': unit_price})
        _logger.info('Parsed %s items from table format', len(items))
        return items

    # --- Brand domain ---
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

    # --- Matching ---
    def _fuzzy_match_score(self, a, b):
        a = _norm_text(a); b = _norm_text(b)
        if HAVE_RF:
            return fuzz.token_set_ratio(a, b)
        return SequenceMatcher(None, a, b).ratio() * 100

    def _token_domain(self, tok):
        return ['|', '|', ('name', 'ilike', tok), ('product_tmpl_id.name', 'ilike', tok), ('default_code', 'ilike', tok)]

    def _search_candidates(self, tokens, langs=("nl_BE", "en_US"), code_tokens=None, brand_name=None):
        Product = self.env['product.product']
        candidates = self.env['product.product']
        brand_dom = self._brand_domain(brand_name, for_model='product.product') if brand_name else []
        tok_list = list(tokens or [])[:10]
        code_list = list(code_tokens or [])[:5]
        per_token_limit = 600
        for lang in langs:
            ctx = {'lang': lang, 'active_test': False}
            for ct in code_list:
                dom = (brand_dom + self._token_domain(ct)) if brand_dom else self._token_domain(ct)
                candidates |= Product.with_context(**ctx).search(dom, limit=per_token_limit)
            for t in tok_list:
                dom = (brand_dom + self._token_domain(t)) if brand_dom else self._token_domain(t)
                candidates |= Product.with_context(**ctx).search(dom, limit=per_token_limit)
        if not candidates and tok_list:
            for lang in langs:
                ctx = {'lang': lang, 'active_test': False}
                for t in tok_list[:2]:
                    dom = (brand_dom + self._token_domain(t)) if brand_dom else self._token_domain(t)
                    candidates |= Product.with_context(**ctx).search(dom, limit=1500)
        _logger.debug('Candidates found: %s (brand=%s)', len(candidates), brand_name)
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
            dom_ilike = [('default_code', 'ilike', c.replace(' ', '').replace('-', ''))]
            if brand_name:
                dom_ilike = self._brand_domain(brand_name, for_model='product.product') + dom_ilike
            cand = Product.with_context(**ctx).search(dom_ilike, limit=200)
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
        """Tokens strikt uit de regel (achter Appliances/Controls), maar mét maat-varianten.
        - families (VR/VRC/VWL/VIH/VP/HB)
        - betekenisvolle woorden (>=3)
        - maat-varianten (8.2, 8,2, 82, 77/8.2, 77 8.2, ...)
        - codes uit de regel
        """
        raw = re.findall(r"[A-Za-z0-9/\.]+", desc or '')
        tokens = []
        for t in raw:
            tl = t.lower()
            if tl in WHITELIST_PREFIXES:
                tokens.append(tl); continue
            if tl in SUFFIX_WHITELIST or (len(tl) > 2 and not tl.isdigit()):
                tokens.append(tl)
        for c in (codes_from_desc or []):
            if c:
                tokens.append(_clean_code(c))
        # maat-varianten op basis van de regel
        text = _norm_text(desc or '')
        size_match = re.search(r"\b\d{1,3}(?:[./]\d{1,2})?(?:\.\d{1,2})?\b", text)
        if size_match:
            size_tok = size_match.group(0)
            size_dot = size_tok.replace(',', '.')
            size_com = size_dot.replace('.', ',')
            for v in {size_tok, size_dot, size_com}:
                if v and v not in tokens:
                    tokens.insert(0, v)
            m = re.match(r"^(\d{1,3})[\/ ](\d{1,2})(?:[.,](\d{1,2}))?$", size_dot)
            if m:
                A, B, C = m.group(1), m.group(2), m.group(3)
                if A not in tokens:
                    tokens.append(A)
                if C:
                    comp = f"{B}.{C}"; comp_com = f"{B},{C}"; flat = f"{B}{C}"
                    for v in {comp, comp_com, flat}:
                        if v not in tokens:
                            tokens.append(v)
                else:
                    if B not in tokens:
                        tokens.append(B)
                spaced = f"{A} {B}.{C}" if C else f"{A} {B}"
                spaced_com = f"{A} {B},{C}" if C else spaced
                flat_all = f"{A}{B}{C}" if C else f"{A}{B}"
                for v in {spaced, spaced_com, flat_all}:
                    if v not in tokens:
                        tokens.append(v)
            else:
                m2 = re.match(r"^(\d{1,2})[.,](\d{1,2})$", size_dot)
                if m2:
                    X, Y = m2.group(1), m2.group(2)
                    flat = f"{X}{Y}"
                    for v in {flat, f"{X}.{Y}", f"{X},{Y}", X}:
                        if v not in tokens:
                            tokens.append(v)
        if not tokens and raw:
            raw_sorted = sorted(set(raw), key=len, reverse=True)
            tokens = [w.lower() for w in raw_sorted[:2]]
        seen = set(); ordered = []
        for t in tokens:
            if t not in seen:
                ordered.append(t); seen.add(t)
        return ordered[:12]

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
            if any(re.search(rf'\b{re.escape(f)}\b', label_n) for f in fams):
                s += 5
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
            if schema_mode:
                text = _norm_text(desc)
                has_family = any(tok in tokens for tok in FAMILY_TOKENS)
                size_match = re.search(r"\b\d{1,3}(?:[./]\d{1,2})?(?:\.\d{1,2})?\b", text)
                if not (has_family or size_match):
                    return (None, 0, 'schema_too_vague', None)
            code_tokens_norm = [re.sub(r"\s+", "", c) for c in (codes_from_desc or [])]
            candidates = self._search_candidates(tokens, code_tokens=code_tokens_norm, brand_name=brand_name)
            if codes_from_desc:
                for p in candidates:
                    label = (p.display_name or p.name or '')
                    label_norm = re.sub(r"[\s\-\.\/]", "", label).upper()
                    for c in codes_from_desc:
                        c_norm = re.sub(r"[\s\/]", "", c).upper()
                        if c_norm and c_norm in label_norm:
                            return (p, 96, 'code_in_name', label)
            top3 = self._rank_candidates(desc, candidates)
            if top3:
                best_score, best_product, best_name = top3[0]
                cutoff = (74 if schema_mode else 75) if HAVE_RF else (66 if schema_mode else 68)
                if best_score >= cutoff:
                    return (best_product, best_score, 'fuzzy', best_name)
                else:
                    return (None, best_score, 'fuzzy_failed', best_name)
        return (None, 0, None, None)

    # --- Action ---
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
            brand_name = (self.brand_filter or '').strip() or None
            if brand_name:
                _logger.info('Brand restriction active on %s via %s', brand_name, BRAND_PROP_FIELD)
            else:
                _logger.info('Brand restriction enabled but no brand provided; proceeding without brand filter')

        parsed_items = self._parse_table_lines(text) if pdf_type == 'table' else self._parse_bottom_bar(text)
        if not parsed_items:
            raise UserError('No items found in PDF. Please check the file format.')

        _logger.info('Parsed %s items from PDF', len(parsed_items))

        matched_lines = []
        unmatched_items = []
        products_dict = {}

        for item in parsed_items:
            if pdf_type == 'table':
                code = item.get('code'); desc = item.get('desc', ''); qty = item.get('qty', 1); unit_price = item.get('unit_price')
            else:
                code = item.get('codes'); desc = item.get('desc', ''); qty = item.get('qty', 1); unit_price = item.get('unit_price')

            product, score, method, candidate_name = self._match_product(
                code=code,
                desc=desc,
                brand_name=brand_name,
                schema_mode=(pdf_type == 'bottom_bar'),
            )

            if product:
                if product.id in products_dict:
                    products_dict[product.id]['qty'] += qty
                else:
                    products_dict[product.id] = {
                        'qty': qty,
                        'price': unit_price if (unit_price and self.use_pdf_prices) else None,
                        'desc': desc if desc else (product.display_name or product.name),
                        'product': product,
                    }
                matched_lines.append(item)
            else:
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
                'name': (product.get_product_multiline_description_sale() if hasattr(product, 'get_product_multiline_description_sale') else (product.display_name or product.name)),
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
