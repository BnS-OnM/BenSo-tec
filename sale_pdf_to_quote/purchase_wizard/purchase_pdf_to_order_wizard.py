from odoo import models, fields
from odoo.exceptions import UserError

import base64
import io
import logging
import re

_logger = logging.getLogger(__name__)

# PDF library
try:
    import PyPDF2
except Exception:
    PyPDF2 = None
    _logger.warning("PyPDF2 not installed - PDF parsing will not work.")

# Fuzzy matcher
try:
    from rapidfuzz import fuzz
    FUZZY_MATCHER = "rapidfuzz"
except Exception:
    from difflib import SequenceMatcher
    FUZZY_MATCHER = "difflib"
    _logger.info("rapidfuzz not available, using difflib fallback")


def _norm_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _norm_lower(s: str) -> str:
    return _norm_text(s).lower()


class PurchasePdfToOrderWizard(models.TransientModel):
    _name = "purchase.pdf.to.order.wizard"
    _description = "Purchase PDF to Order Wizard"

    pdf_file = fields.Binary(string="PDF File", required=True)
    filename = fields.Char(string="Filename")

    vendor_id = fields.Many2one(
        "res.partner",
        string="Vendor (optional)",
        domain=[("is_company", "=", True)],
        help="Als leeg: wizard probeert vendor uit PDF te detecteren en anders wordt een nieuwe vendor aangemaakt."
    )

    name_prefix = fields.Char(
        string="Reference Prefix",
        default="PDF-PO",
        help="Prefix voor traceability in origin / partner_ref"
    )

    # Result
    purchase_order_id = fields.Many2one("purchase.order", string="Created RFQ/PO", readonly=True)
    matched_count = fields.Integer(string="Matched Lines", readonly=True)
    created_products_count = fields.Integer(string="New Products", readonly=True)
    unmatched_count = fields.Integer(string="Unmatched Lines", readonly=True)
    unmatched_text = fields.Text(string="Unmatched Details", readonly=True)
    state = fields.Selection([("draft", "Draft"), ("done", "Done")], default="draft")

    # ---------------- PDF extraction ----------------

    def _extract_text_from_pdf(self, pdf_bytes: bytes) -> str:
        if not PyPDF2:
            raise UserError("PyPDF2 library is not installed. Add PyPDF2==2.12.1 to requirements.txt")

        try:
            pdf_file_obj = io.BytesIO(pdf_bytes)
            pdf_reader = PyPDF2.PdfReader(pdf_file_obj)

            text_content = []
            for page in pdf_reader.pages:
                t = page.extract_text()
                if t:
                    text_content.append(t)

            full_text = "\n".join(text_content)
            if not full_text or len(full_text.strip()) < 50:
                raise UserError("PDF bevat geen leesbare tekst (waarschijnlijk scan). Upload een tekst-PDF.")
            return full_text
        except UserError:
            raise
        except Exception as e:
            _logger.exception("Error extracting PDF text")
            raise UserError(f"Failed to extract text from PDF: {e}")

    # ---------------- Parsing helpers ----------------

    def _parse_eu_number(self, s):
        """EU: 1.234,56 -> 1234.56 | 123,45 -> 123.45"""
        if not s:
            return None
        try:
            cleaned = s.strip()
            cleaned = cleaned.replace(" ", "")
            cleaned = cleaned.replace(".", "").replace(",", ".")
            return float(cleaned)
        except Exception:
            return None

    def _parse_discount_percent(self, s):
        if not s:
            return None
        s = s.strip().replace(" ", "")
        m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*%", s)
        if not m:
            return None
        val = self._parse_eu_number(m.group(1))
        return val

    def _detect_vendor_name(self, text: str):
        """
        Zeer simpele heuristiek. Als je PDF’s vaste labels hebben (Supplier/Vendor),
        kan je dit later verfijnen.
        """
        tl = _norm_lower(text)

        # typische labels
        patterns = [
            r"(?:vendor|supplier|leverancier)\s*[:\-]\s*(.+)",
            r"(?:from)\s*[:\-]\s*(.+)",
        ]
        for pat in patterns:
            m = re.search(pat, tl, re.IGNORECASE)
            if m:
                name = _norm_text(m.group(1))
                # stop aan lijn-einde / dubbele spaties
                name = name.split("\n")[0].strip()
                # afkappen als er extra stukjes zijn
                name = re.split(r"\s{2,}", name)[0].strip()
                if len(name) >= 2:
                    return name

        # fallback: soms staat vendor bovenaan in eerste regels
        first_lines = [l.strip() for l in text.split("\n")[:10] if l.strip()]
        if first_lines:
            # als eerste lijn lijkt op bedrijfsnaam
            cand = first_lines[0].strip()
            if 2 <= len(cand) <= 80 and not re.search(r"\d{3,}", cand):
                return cand
        
        return None

    # ---------------- Main action ----------------

    def action_create_purchase_order(self):
        """
        Main action to create a purchase order from the PDF.
        This is a placeholder implementation that needs to be completed
        based on the actual business requirements.
        """
        self.ensure_one()

        if not self.pdf_file:
            raise UserError("Please upload a PDF file.")

        # For now, just update the state to show completion
        # TODO: Implement full purchase order creation logic
        self.write({
            "state": "done",
            "matched_count": 0,
            "created_products_count": 0,
            "unmatched_count": 0,
            "unmatched_text": "Implementation pending",
        })

        return {
            "type": "ir.actions.act_window",
            "res_model": "purchase.pdf.to.order.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
