"""Structured, source-policy-aware document reader for Editorial V3."""

from __future__ import annotations

import hashlib
import ipaddress
from io import BytesIO
import json
import re
import socket
import uuid
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag
from pypdf import PdfReader

from app.schemas.editorial_v3 import (
    ResearchSourceSignals,
    SourceOwnershipType,
    SourcePageType,
)
from app.schemas.editorial_v3_runtime import (
    StructuredDocumentSection,
    StructuredSourceDocument,
    StructuredTable,
)
from app.services.editorial_v3.source_policy import ResearchSourcePolicyService
from app.services.research_engine import (
    SearchDocument,
    canonicalize_url,
    parse_published_at,
)


_WS = re.compile(r"\s+")
_PRICE = re.compile(r"(?:R\$|US\$|€|£|\$)\s*\d", re.I)
_ADD_TO_CART = re.compile(
    r"\b(?:add to cart|buy now|comprar agora|adicionar ao carrinho|compre agora)\b",
    re.I,
)
_REFERENCES = re.compile(
    r"\b(?:references|bibliography|referências|bibliografia|works cited)\b", re.I
)
_RESEARCH_TERMS = re.compile(
    r"\b(?:abstract|methodology|results?|discussion|doi|peer[- ]reviewed|"
    r"resumo|metodologia|resultados?|discussão)\b",
    re.I,
)
_ECOMMERCE_PLATFORM = re.compile(
    r"(?i)\b(?:shopify|woocommerce|magento|prestashop|bigcommerce|nuvemshop|tray commerce)\b"
)
_PROCEDURAL_TERMS = re.compile(
    r"\b(?:step|passo|materials?|materiais|prepare|prepar|observe|sinal|erro|"
    r"troubleshoot|como fazer|procedimento|transplant|transfer)\b",
    re.I,
)
_MARKETPLACE_HOSTS = {
    "amazon.",
    "mercadolivre.",
    "ebay.",
    "aliexpress.",
    "shopee.",
    "etsy.",
}
_ACADEMIC_HOST_MARKERS = (
    ".edu",
    ".ac.",
    "scielo",
    "pubmed",
    "ncbi.nlm.nih.gov",
    "researchgate",
    "springer",
    "wiley",
    "nature.com",
    "science.org",
    "frontiersin.org",
    "mdpi.com",
    "jstor",
)
_NEWS_HOST_MARKERS = (
    "reuters",
    "apnews",
    "bbc.",
    "agenciabrasil",
    "theguardian",
    "nytimes",
    "folha.uol",
    "estadao",
)
_ENCYCLOPEDIA_HOST_MARKERS = ("wikipedia.org", "britannica.com")
_COMMUNITY_HOST_MARKERS = ("reddit.com", "quora.com", "forum", "stackexchange")


class UnsafeSourceURL(ValueError):
    pass


class SourceDocumentParser:
    """Fetch and decompose a source while retaining headings, lists and tables.

    The parser never uses search rank.  It derives source signals from the page
    itself and submits them to the deterministic V3 source policy.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_bytes: int = 2_500_000,
        max_text_characters: int = 100_000,
        client_factory=None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_text_characters = max_text_characters
        self._client_factory = client_factory or httpx.AsyncClient
        self.policy = ResearchSourcePolicyService()

    async def read(self, document: SearchDocument) -> StructuredSourceDocument:
        body: bytes | None = None
        headers: dict[str, str] = {}
        warnings: list[str] = []
        canonical = canonicalize_url(document.url)
        try:
            self._validate_public_url(canonical)
            async with self._client_factory(
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=False,
                headers={"User-Agent": "SEOResearchLedger/3.0 (+editorial-research)"},
            ) as client:
                current = canonical
                final_url = canonical
                for _ in range(6):
                    self._validate_public_url(current)
                    stream_factory = getattr(client, "stream", None)
                    if callable(stream_factory):
                        async with stream_factory("GET", current) as response:
                            status_code = int(response.status_code)
                            response_headers = dict(response.headers)
                            response_url = str(response.url)
                            if status_code in {301, 302, 303, 307, 308}:
                                location = response_headers.get("location")
                                if not location:
                                    raise ValueError(
                                        "Redirect response has no Location header"
                                    )
                                current = canonicalize_url(urljoin(current, location))
                                continue
                            response.raise_for_status()
                            final_url = canonicalize_url(response_url)
                            self._validate_public_url(final_url)
                            headers = {
                                key.lower(): value
                                for key, value in response_headers.items()
                            }
                            content_length = int(
                                headers.get("content-length") or 0
                            )
                            if content_length and content_length > self.max_bytes:
                                warnings.append("source_content_length_exceeded")
                            chunks = bytearray()
                            truncated = False
                            async for chunk in response.aiter_bytes():
                                remaining = self.max_bytes - len(chunks)
                                if remaining <= 0:
                                    truncated = True
                                    break
                                if len(chunk) > remaining:
                                    chunks.extend(chunk[:remaining])
                                    truncated = True
                                    break
                                chunks.extend(chunk)
                            body = bytes(chunks)
                            if truncated:
                                warnings.append("source_body_truncated")
                            break
                    else:
                        # Test doubles and custom clients may implement only
                        # ``get``. Production httpx clients always use the
                        # bounded streaming path above.
                        response = await client.get(current)
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise ValueError(
                                    "Redirect response has no Location header"
                                )
                            current = canonicalize_url(urljoin(current, location))
                            continue
                        response.raise_for_status()
                        final_url = canonicalize_url(str(response.url))
                        self._validate_public_url(final_url)
                        headers = {
                            key.lower(): value
                            for key, value in response.headers.items()
                        }
                        content_length = int(
                            headers.get("content-length") or 0
                        )
                        if content_length and content_length > self.max_bytes:
                            warnings.append("source_content_length_exceeded")
                        response_content = bytes(response.content)
                        body = response_content[: self.max_bytes]
                        if len(response_content) > self.max_bytes:
                            warnings.append("source_body_truncated")
                        break
                else:
                    raise ValueError("Too many source redirects")
                canonical = final_url
        except (httpx.HTTPError, OSError, ValueError, UnsafeSourceURL):
            warnings.append("live_fetch_unavailable_used_search_snapshot")

        content_type = headers.get("content-type", "").lower()
        if body and (
            "application/pdf" in content_type or canonical.lower().endswith(".pdf")
        ):
            try:
                parsed = self.parse_pdf(
                    body,
                    source=document,
                    final_url=canonical,
                    warnings=warnings,
                )
                if len(parsed.plain_text.strip()) >= 100:
                    return parsed
            except Exception:
                warnings.append("pdf_text_extraction_failed")
        if body and "html" in (content_type or "text/html"):
            html_text = body.decode("utf-8", errors="replace")
            return self.parse_html(
                html_text,
                source=document,
                final_url=canonical,
                warnings=warnings,
            )
        return self.parse_text(document, final_url=canonical, warnings=warnings)

    def parse_pdf(
        self,
        content: bytes,
        *,
        source: SearchDocument,
        final_url: str | None = None,
        warnings: list[str] | None = None,
    ) -> StructuredSourceDocument:
        warnings = list(warnings or [])
        canonical = canonicalize_url(final_url or source.url)
        reader = PdfReader(BytesIO(content))
        metadata = reader.metadata or {}
        sections: list[StructuredDocumentSection] = []
        page_texts: list[str] = []
        for index, page in enumerate(reader.pages[:300], start=1):
            text = self._clean(page.extract_text() or "")
            if len(text) < 30:
                continue
            page_texts.append(text)
            sections.append(
                StructuredDocumentSection(
                    section_id="sec_"
                    + hashlib.sha256(
                        f"{canonical}:page:{index}:{text[:200]}".encode()
                    ).hexdigest()[:12],
                    heading_path=[f"Página {index}"],
                    paragraphs=[text],
                    source_locator=f"pdf:page:{index}",
                    character_count=len(text),
                )
            )
        plain_text = "\n\n".join(page_texts)
        if not plain_text:
            raise ValueError("PDF contains no extractable text")
        truncated = len(plain_text) > self.max_text_characters
        if truncated:
            plain_text = plain_text[: self.max_text_characters]
            warnings.append("structured_text_truncated")
        page_type, ownership, ecommerce = self._classify_page(
            canonical, {}, None, plain_text
        )
        signals = self._signals(
            canonical,
            {
                "title": str(metadata.get("/Title") or source.title),
                "author": str(metadata.get("/Author") or source.author or ""),
            },
            None,
            plain_text,
            page_type=page_type,
            ownership=ownership,
            ecommerce=ecommerce,
            references=[],
        )
        assessment = self.policy.assess(signals)
        return StructuredSourceDocument(
            document_id=uuid.uuid5(uuid.NAMESPACE_URL, canonical + source.content_hash),
            url=source.url,
            canonical_url=canonical,
            title=str(metadata.get("/Title") or source.title or canonical),
            author=str(metadata.get("/Author") or source.author or "") or None,
            publisher=source.publisher,
            published_at=source.published_at,
            accessed_at=source.accessed_at,
            language=source.search_language,
            document_type=page_type,
            content_hash=hashlib.sha256(plain_text.encode()).hexdigest(),
            sections=sections,
            bibliographic_references=[],
            outgoing_links=[],
            assessment=assessment,
            source_signals=signals,
            truncated=truncated,
            warnings=list(dict.fromkeys(warnings)),
            plain_text=plain_text,
        )

    def parse_html(
        self,
        html_text: str,
        *,
        source: SearchDocument,
        final_url: str | None = None,
        warnings: list[str] | None = None,
    ) -> StructuredSourceDocument:
        warnings = list(warnings or [])
        soup = BeautifulSoup(html_text, "html.parser")
        metadata = self._metadata(soup, source)
        canonical = canonicalize_url(
            final_url or metadata.get("canonical") or source.url
        )
        root = self._main_root(soup)
        for tag in root.find_all(
            ["script", "style", "nav", "footer", "aside", "form", "noscript", "svg"]
        ):
            tag.decompose()
        # Hidden text is not visible editorial evidence and is a common carrier
        # for SEO spam or instruction injection. Remove it before sectioning.
        for tag in list(root.find_all(True)):
            style = str(tag.get("style") or "").casefold().replace(" ", "")
            aria_hidden = str(tag.get("aria-hidden") or "").casefold() == "true"
            if (
                tag.has_attr("hidden")
                or aria_hidden
                or "display:none" in style
                or "visibility:hidden" in style
                or "opacity:0" in style
            ):
                tag.decompose()

        sections = self._sections(root)
        plain_text = "\n\n".join(
            "\n".join(
                [
                    *section.heading_path,
                    *section.paragraphs,
                    *section.ordered_steps,
                    *section.unordered_items,
                ]
            )
            for section in sections
        ).strip()
        truncated = len(plain_text) > self.max_text_characters
        if truncated:
            warnings.append("structured_text_truncated")
            plain_text = plain_text[: self.max_text_characters]

        outgoing_links = self._links(root, canonical)
        references = self._bibliography(root)
        page_type, ownership, ecommerce = self._classify_page(
            canonical, metadata, soup, plain_text
        )
        signals = self._signals(
            canonical,
            metadata,
            soup,
            plain_text,
            page_type=page_type,
            ownership=ownership,
            ecommerce=ecommerce,
            references=references,
        )
        assessment = self.policy.assess(signals)
        if assessment.usage_policy.value == "rejected":
            warnings.append("source_rejected_by_policy")

        return StructuredSourceDocument(
            document_id=uuid.uuid5(uuid.NAMESPACE_URL, canonical + source.content_hash),
            url=source.url,
            canonical_url=canonical,
            title=metadata.get("title") or source.title or canonical,
            author=metadata.get("author") or source.author,
            publisher=metadata.get("publisher") or source.publisher,
            published_at=parse_published_at(metadata.get("published_at"))
            or source.published_at,
            accessed_at=source.accessed_at,
            language=metadata.get("language"),
            document_type=page_type,
            content_hash=hashlib.sha256(plain_text.encode("utf-8")).hexdigest(),
            sections=sections,
            bibliographic_references=references,
            outgoing_links=outgoing_links,
            assessment=assessment,
            source_signals=signals,
            truncated=truncated,
            warnings=list(dict.fromkeys(warnings)),
            plain_text=plain_text,
        )

    def parse_text(
        self,
        source: SearchDocument,
        *,
        final_url: str | None = None,
        warnings: list[str] | None = None,
    ) -> StructuredSourceDocument:
        warnings = list(warnings or [])
        canonical = canonicalize_url(final_url or source.url)
        paragraphs = [
            value.strip()
            for value in re.split(r"\n\s*\n|(?<=[.!?])\s{2,}", source.content)
            if len(value.strip()) >= 30
        ]
        if not paragraphs:
            paragraphs = [source.content.strip()]
        text = "\n\n".join(paragraphs)
        truncated = len(text) > self.max_text_characters
        text = text[: self.max_text_characters]
        page_type, ownership, ecommerce = self._classify_page(canonical, {}, None, text)
        signals = self._signals(
            canonical,
            {"title": source.title, "author": source.author},
            None,
            text,
            page_type=page_type,
            ownership=ownership,
            ecommerce=ecommerce,
            references=[],
        )
        assessment = self.policy.assess(signals)
        section = StructuredDocumentSection(
            section_id="sec_" + hashlib.sha256(text[:1000].encode()).hexdigest()[:12],
            heading_path=[source.title],
            paragraphs=paragraphs[:100],
            source_locator="snapshot:text",
            character_count=len(text),
        )
        return StructuredSourceDocument(
            document_id=uuid.uuid5(uuid.NAMESPACE_URL, canonical + source.content_hash),
            url=source.url,
            canonical_url=canonical,
            title=source.title or canonical,
            author=source.author,
            publisher=source.publisher,
            published_at=source.published_at,
            accessed_at=source.accessed_at,
            language=source.search_language,
            document_type=page_type,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            sections=[section],
            bibliographic_references=[],
            outgoing_links=[],
            assessment=assessment,
            source_signals=signals,
            truncated=truncated,
            warnings=list(dict.fromkeys([*warnings, "unstructured_snapshot_fallback"])),
            plain_text=text,
        )

    @staticmethod
    def _main_root(soup: BeautifulSoup) -> Tag:
        candidates = soup.select(
            "article, main, [role=main], .article-content, .entry-content, .post-content, .content"
        )
        if candidates:
            return max(candidates, key=lambda item: len(item.get_text(" ", strip=True)))
        return soup.body or soup

    def _sections(self, root: Tag) -> list[StructuredDocumentSection]:
        sections: list[dict] = []
        heading_stack: list[str] = []
        current = self._new_section(heading_stack, 0)
        seen_text: set[str] = set()

        for element in root.find_all(
            ["h1", "h2", "h3", "h4", "p", "ol", "ul", "table"]
        ):
            if element.find_parent(["nav", "footer", "aside", "form"]):
                continue
            if element.name in {"h1", "h2", "h3", "h4"}:
                heading = self._clean(element.get_text(" ", strip=True))
                if not heading:
                    continue
                if self._section_has_content(current):
                    sections.append(current)
                level = int(element.name[1])
                heading_stack = heading_stack[: max(0, level - 1)]
                while len(heading_stack) < level - 1:
                    heading_stack.append(heading_stack[-1] if heading_stack else "")
                heading_stack.append(heading)
                current = self._new_section(heading_stack, len(sections))
                continue
            if element.name == "p":
                text = self._clean(element.get_text(" ", strip=True))
                if len(text) >= 30 and text not in seen_text:
                    current["paragraphs"].append(text)
                    seen_text.add(text)
            elif element.name in {"ol", "ul"}:
                values = []
                for li in element.find_all("li", recursive=False):
                    text = self._clean(li.get_text(" ", strip=True))
                    if len(text) >= 5 and text not in seen_text:
                        values.append(text)
                        seen_text.add(text)
                key = "ordered_steps" if element.name == "ol" else "unordered_items"
                current[key].extend(values)
            elif element.name == "table":
                parsed = self._table(element)
                if parsed.rows or parsed.headers:
                    current["tables"].append(parsed)

        if self._section_has_content(current):
            sections.append(current)
        if not sections:
            text = self._clean(root.get_text(" ", strip=True))
            sections = [self._new_section([], 0)]
            sections[0]["paragraphs"] = [text]

        result: list[StructuredDocumentSection] = []
        for index, item in enumerate(sections):
            locator = (
                " > ".join(item["heading_path"]) or f"document section {index + 1}"
            )
            content = " ".join(
                [
                    *item["paragraphs"],
                    *item["ordered_steps"],
                    *item["unordered_items"],
                ]
            )
            key = hashlib.sha256((locator + content[:500]).encode()).hexdigest()[:12]
            result.append(
                StructuredDocumentSection(
                    section_id="sec_" + key,
                    heading_path=item["heading_path"],
                    paragraphs=item["paragraphs"][:100],
                    ordered_steps=item["ordered_steps"][:100],
                    unordered_items=item["unordered_items"][:100],
                    tables=item["tables"][:20],
                    source_locator=locator,
                    character_count=len(content),
                )
            )
        return result[:300]

    @staticmethod
    def _new_section(heading_path: list[str], index: int) -> dict:
        return {
            "heading_path": [value for value in heading_path if value],
            "paragraphs": [],
            "ordered_steps": [],
            "unordered_items": [],
            "tables": [],
            "index": index,
        }

    @staticmethod
    def _section_has_content(section: dict) -> bool:
        return any(
            section[key]
            for key in ("paragraphs", "ordered_steps", "unordered_items", "tables")
        )

    def _table(self, table: Tag) -> StructuredTable:
        caption = (
            self._clean(table.caption.get_text(" ", strip=True))
            if table.caption
            else ""
        )
        rows = []
        headers = []
        for tr in table.find_all("tr"):
            ths = [
                self._clean(cell.get_text(" ", strip=True))
                for cell in tr.find_all("th")
            ]
            tds = [
                self._clean(cell.get_text(" ", strip=True))
                for cell in tr.find_all("td")
            ]
            if ths and not headers:
                headers = ths
            elif tds:
                rows.append(tds)
        return StructuredTable(caption=caption, headers=headers[:30], rows=rows[:100])

    def _metadata(self, soup: BeautifulSoup, source: SearchDocument) -> dict[str, str]:
        result: dict[str, str] = {}
        html = soup.find("html")
        if html and html.get("lang"):
            result["language"] = str(html.get("lang"))[:20]
        canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
        if canonical and canonical.get("href"):
            result["canonical"] = str(canonical.get("href"))
        for key, selectors in {
            "title": [
                ('meta[property="og:title"]', "content"),
                ('meta[name="twitter:title"]', "content"),
            ],
            "author": [
                ('meta[name="author"]', "content"),
                ('meta[property="article:author"]', "content"),
            ],
            "publisher": [('meta[property="og:site_name"]', "content")],
            "published_at": [
                ('meta[property="article:published_time"]', "content"),
                ('meta[name="date"]', "content"),
                ("time[datetime]", "datetime"),
            ],
        }.items():
            for selector, attribute in selectors:
                tag = soup.select_one(selector)
                if tag and tag.get(attribute):
                    result[key] = self._clean(str(tag.get(attribute)))
                    break
        if "title" not in result and soup.title:
            result["title"] = self._clean(soup.title.get_text(" ", strip=True))
        result.setdefault("title", source.title)
        return result

    def _classify_page(
        self,
        url: str,
        metadata: dict,
        soup: BeautifulSoup | None,
        text: str,
    ) -> tuple[SourcePageType, SourceOwnershipType, bool]:
        host = (urlsplit(url).hostname or "").lower()
        path = urlsplit(url).path.lower()
        lowered = text[:50000].lower()
        schema_types = self._jsonld_types(soup) if soup else set()
        marketplace = any(marker in host for marker in _MARKETPLACE_HOSTS)
        has_commerce_schema = bool(
            {"product", "offer", "itemlist"}.intersection(schema_types)
        )
        has_cart = bool(_ADD_TO_CART.search(lowered))
        platform_text = " ".join(
            str(tag.get("content") or "")
            for tag in (soup.find_all("meta") if soup else [])
        )
        commerce_links = (
            any(
                re.search(
                    r"(?i)/(?:cart|carrinho|checkout|shop|store|products?|produtos?)(?:/|$)",
                    str(anchor.get("href") or ""),
                )
                for anchor in soup.find_all("a", href=True)
            )
            if soup
            else False
        )
        commerce_host = host.startswith(("shop.", "store.", "loja."))
        ecommerce = any(
            (
                marketplace,
                has_commerce_schema,
                has_cart,
                bool(_PRICE.search(lowered)),
                bool(_ECOMMERCE_PLATFORM.search(platform_text)),
                commerce_links,
                commerce_host,
            )
        )

        if marketplace:
            return (
                SourcePageType.marketplace_listing,
                SourceOwnershipType.marketplace,
                True,
            )
        if host.endswith(".gov") or ".gov." in host:
            return (
                SourcePageType.institutional_article,
                SourceOwnershipType.public_institution,
                False,
            )
        if any(marker in host for marker in _ACADEMIC_HOST_MARKERS):
            if _RESEARCH_TERMS.search(lowered) or "scholarlyarticle" in schema_types:
                return (
                    SourcePageType.research_article,
                    SourceOwnershipType.scientific_publisher,
                    False,
                )
            return SourcePageType.technical_guide, SourceOwnershipType.academic, False
        if any(marker in host for marker in _ENCYCLOPEDIA_HOST_MARKERS):
            return (
                SourcePageType.encyclopedia_article,
                SourceOwnershipType.encyclopedia,
                False,
            )
        if any(marker in host for marker in _NEWS_HOST_MARKERS):
            return (
                SourcePageType.news_article,
                SourceOwnershipType.news_organization,
                False,
            )
        if any(marker in host for marker in _COMMUNITY_HOST_MARKERS):
            return SourcePageType.forum_thread, SourceOwnershipType.community, False
        if ecommerce:
            if any(
                marker in path for marker in ("/blog/", "/article", "/guide", "/learn/")
            ):
                return (
                    SourcePageType.ecommerce_blog_article,
                    SourceOwnershipType.ecommerce,
                    True,
                )
            if any(
                marker in path
                for marker in (
                    "/product",
                    "/produto",
                    "/shop",
                    "/store",
                    "/category",
                    "/categoria",
                )
            ):
                return SourcePageType.product_page, SourceOwnershipType.ecommerce, True
            return (
                SourcePageType.commercial_landing_page,
                SourceOwnershipType.ecommerce,
                True,
            )
        # Generic pages often contain words such as "results" or
        # "methodology" without being peer-reviewed research.  Do not promote an
        # independent website to scientific authority from vocabulary alone.
        # Its research signals still contribute to the multidimensional score,
        # while the source remains corroborating unless the host or ownership is
        # independently known to be academic/institutional.
        if _PROCEDURAL_TERMS.search(lowered):
            return (
                SourcePageType.technical_guide,
                SourceOwnershipType.independent_editorial,
                False,
            )
        return (
            SourcePageType.independent_article,
            SourceOwnershipType.independent_editorial,
            False,
        )

    def _signals(
        self,
        url: str,
        metadata: dict,
        soup: BeautifulSoup | None,
        text: str,
        *,
        page_type: SourcePageType,
        ownership: SourceOwnershipType,
        ecommerce: bool,
        references: list[str],
    ) -> ResearchSourceSignals:
        lowered = text.lower()
        schema_types = self._jsonld_types(soup) if soup else set()
        word_count = len(text.split())
        ordered_lists = len(soup.find_all("ol")) if soup else 0
        headings = len(soup.find_all(["h2", "h3"])) if soup else 0
        procedure_hits = len(_PROCEDURAL_TERMS.findall(text))
        research_hits = len(_RESEARCH_TERMS.findall(text))
        procedural_depth = min(
            1.0, (ordered_lists * 0.2) + (procedure_hits / 25) + (headings / 30)
        )
        scientific_support = min(
            1.0, (research_hits / 20) + (0.35 if references else 0)
        )
        content_depth = min(1.0, word_count / 1800)
        commercial_hits = sum(
            int(value)
            for value in (
                bool(_PRICE.search(text)),
                bool(_ADD_TO_CART.search(text)),
                "product" in schema_types,
                "offer" in schema_types,
                "checkout" in lowered,
            )
        )
        return ResearchSourceSignals(
            url=url,
            title=metadata.get("title", ""),
            ownership_type=ownership,
            page_type=page_type,
            is_ecommerce_domain=ecommerce,
            has_product_schema="product" in schema_types,
            has_offer_schema="offer" in schema_types,
            has_price=bool(_PRICE.search(text)),
            has_sku="sku" in lowered,
            has_add_to_cart=bool(_ADD_TO_CART.search(text)),
            has_cart_or_checkout_links="cart" in lowered
            or "checkout" in lowered
            or "carrinho" in lowered,
            marketplace_signals=ownership == SourceOwnershipType.marketplace,
            author_present=bool(metadata.get("author")),
            publication_date_present=bool(metadata.get("published_at")),
            references_present=bool(references or _REFERENCES.search(text)),
            peer_reviewed="peer reviewed" in lowered or "peer-reviewed" in lowered,
            primary_research=page_type == SourcePageType.research_article,
            review_research=page_type == SourcePageType.review_article,
            institutional_affiliation=ownership
            in {
                SourceOwnershipType.academic,
                SourceOwnershipType.public_institution,
                SourceOwnershipType.nonprofit_institution,
            },
            commercial_intensity_score=min(1.0, commercial_hits / 4),
            content_depth_score=content_depth,
            procedural_depth_score=procedural_depth,
            scientific_support_score=scientific_support,
            freshness_score=0.5,
            topic_relevance_score=min(
                1.0, max(0.25, content_depth * 0.55 + procedural_depth * 0.45)
            ),
        )

    def _links(self, root: Tag, base_url: str) -> list[str]:
        result = []
        for anchor in root.find_all("a", href=True):
            href = urljoin(base_url, str(anchor.get("href")))
            if href.startswith(("http://", "https://")):
                result.append(canonicalize_url(href))
        return list(dict.fromkeys(result))[:300]

    def _bibliography(self, root: Tag) -> list[str]:
        result = []
        for heading in root.find_all(["h2", "h3", "h4"]):
            if not _REFERENCES.search(heading.get_text(" ", strip=True)):
                continue
            for sibling in heading.find_all_next(["li", "p"], limit=100):
                if sibling.name and sibling.name.startswith("h"):
                    break
                text = self._clean(sibling.get_text(" ", strip=True))
                if len(text) >= 20:
                    result.append(text)
        return list(dict.fromkeys(result))[:200]

    @staticmethod
    def _jsonld_types(soup: BeautifulSoup | None) -> set[str]:
        if soup is None:
            return set()
        result: set[str] = set()
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                payload = json.loads(script.string or script.get_text() or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            stack = payload if isinstance(payload, list) else [payload]
            while stack:
                item = stack.pop()
                if isinstance(item, list):
                    stack.extend(item)
                elif isinstance(item, dict):
                    raw_type = item.get("@type")
                    values = raw_type if isinstance(raw_type, list) else [raw_type]
                    result.update(str(value).lower() for value in values if value)
                    stack.extend(item.values())
        return result

    @staticmethod
    def _clean(value: str) -> str:
        return _WS.sub(" ", value or "").strip()

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise UnsafeSourceURL("Only public HTTP(S) source URLs are allowed")
        host = parts.hostname.lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise UnsafeSourceURL("Local addresses are forbidden")
        try:
            addresses = socket.getaddrinfo(
                host, parts.port or 443, type=socket.SOCK_STREAM
            )
        except socket.gaierror:
            return
        for info in addresses:
            address = ipaddress.ip_address(info[4][0])
            if not address.is_global:
                raise UnsafeSourceURL(
                    "Private or non-global source addresses are forbidden"
                )
