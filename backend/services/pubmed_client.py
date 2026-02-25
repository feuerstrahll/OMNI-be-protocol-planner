from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote
from xml.etree import ElementTree

from backend.schemas import SourceCandidate
from backend.services.utils import (
    AppConfig,
    get_cache,
    normalize_space,
    request_json_with_cache,
    request_text_with_cache,
)


# 2-step search: if Step A returns fewer than this, run Step B (broader)
_MIN_RESULTS_STEP_A = 3

# Thematic markers: PK/BE/forms/quality (must appear in query)
_THEMATIC_TERMS = (
    "bioequivalence[tiab] OR bioavailability[tiab] OR pharmacokinetics[tiab] OR "
    "pharmacokinetics[MeSH Terms] OR "
    "\"delayed release\"[tiab] OR enteric[tiab] OR \"enteric-coated\"[tiab] OR "
    "formulation[tiab] OR capsule[tiab] OR tablet[tiab] OR "
    "dissolution[tiab] OR generic[tiab] OR "
    "\"healthy volunteers\"[tiab] OR \"healthy subjects\"[tiab] OR crossover[tiab] OR "
    "Cmax[tiab] OR AUC[tiab]"
)

# Anti-topics: exclude (probe/DDI/veterinary etc.)
_ANTI_TERMS = (
    "phenotyping[tiab] OR phenotype[tiab] OR probe[tiab] OR cocktail[tiab] OR "
    "microdose[tiab] OR veterinary[tiab] OR "
    "horse[tiab] OR equine[tiab] OR cat[tiab] OR feline[tiab] OR dog[tiab] OR canine[tiab] OR "
    "rat[tiab] OR mice[tiab] OR mouse[tiab] OR pigs[tiab] OR swine[tiab]"
)

# PMC uses [Title/Abstract] for text fields
_PMC_THEMATIC = (
    "bioequivalence[Title/Abstract] OR bioavailability[Title/Abstract] OR pharmacokinetics[Title/Abstract] OR "
    "\"delayed release\"[Title/Abstract] OR enteric[Title/Abstract] OR \"enteric-coated\"[Title/Abstract] OR "
    "formulation[Title/Abstract] OR capsule[Title/Abstract] OR tablet[Title/Abstract] OR "
    "dissolution[Title/Abstract] OR generic[Title/Abstract] OR "
    "\"healthy volunteers\"[Title/Abstract] OR \"healthy subjects\"[Title/Abstract] OR crossover[Title/Abstract] OR "
    "Cmax[Title/Abstract] OR AUC[Title/Abstract]"
)
_PMC_ANTI = (
    "phenotyping[Title/Abstract] OR phenotype[Title/Abstract] OR probe[Title/Abstract] OR cocktail[Title/Abstract] OR "
    "microdose[Title/Abstract] OR veterinary[Title/Abstract] OR "
    "horse[Title/Abstract] OR equine[Title/Abstract] OR cat[Title/Abstract] OR feline[Title/Abstract] OR "
    "dog[Title/Abstract] OR canine[Title/Abstract] OR rat[Title/Abstract] OR mice[Title/Abstract] OR "
    "mouse[Title/Abstract] OR pigs[Title/Abstract] OR swine[Title/Abstract]"
)

# Species: only humans (classic PubMed filter)
_HUMANS_ONLY = "NOT (animals[mh] NOT humans[mh])"

# Scoring: theme keywords (+3 in title, +1 in abstract)
_THEME_KEYWORDS = (
    "bioequivalence", "bioavailability", "pharmacokinetics", "delayed release", "enteric",
    "enteric-coated", "formulation", "capsule", "tablet", "dissolution", "generic",
    "healthy volunteers", "healthy subjects", "crossover", "cmax", "auc",
)
# Must-have for +2 (any of these)
_MUST_KEYWORDS = ("delayed-release", "delayed release", "enteric", "dissolution")
# Anti: -10 in title, -5 only in abstract
_ANTI_KEYWORDS = ("phenotyping", "phenotype", "probe", "cocktail", "microdose")
# Score threshold: drop articles below this
_SCORE_THRESHOLD = 3

# Official/regulatory sources (always appended to search_sources). INN-specific URLs for omeprazole.
_OFFICIAL_SOURCES_OMEPRAZOLE = (
    (
        "FDA label (Prilosec / omeprazole delayed-release)",
        "https://www.accessdata.fda.gov/drugsatfda_docs/label/2023/022056s026lbl.pdf",
    ),
    (
        "EMA SmPC (Losec / omeprazole)",
        "https://www.ema.europa.eu/en/medicines/human/EPAR/losec",
    ),
    (
        "DailyMed (generic omeprazole delayed-release)",
        "https://dailymed.nlm.nih.gov/dailymed/search.cfm?query=omeprazole+delayed+release",
    ),
    (
        "BNF (NICE) omeprazole dosing",
        "https://bnf.nice.org.uk/drugs/omeprazole/",
    ),
)


def _parse_ref_id(ref: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse ref_id into (id_type, id_value, normalized_ref_id). Returns (None,None,None) if invalid."""
    s = (ref or "").strip()
    if not s:
        return None, None, None
    u = s.upper()
    if u.startswith("PMCID:"):
        raw = s.split(":", 1)[1].strip().lstrip("PMC")
        return "PMCID", raw, f"PMCID:{raw}" if raw else (None, None, None)
    if u.startswith("PMID:"):
        raw = s.split(":", 1)[1].strip()
        return "PMID", raw, f"PMID:{raw}" if raw else (None, None, None)
    if u.startswith("URL:") or s.startswith("http://") or s.startswith("https://"):
        url = s.split(":", 1)[1].strip() if u.startswith("URL:") else s
        return "URL", url, f"URL:{url}"
    if s.isdigit():
        return "PMID", s, f"PMID:{s}"
    return "PMID", s, f"PMID:{s}"  # legacy: treat as PMID


def _get_official_sources(inn: str) -> List[SourceCandidate]:
    """Return 4 official/regulatory sources (id_type=URL). Always included in /search_sources."""
    inn_lower = (inn or "").strip().lower()
    if inn_lower == "omeprazole":
        items = _OFFICIAL_SOURCES_OMEPRAZOLE
    else:
        # Generic search URLs for other INNs
        enc = quote(inn_lower or "drug")
        items = (
            (f"FDA label ({inn_lower})", f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.processSearch&term={enc}"),
            (f"EMA SmPC ({inn_lower})", "https://www.ema.europa.eu/en/medicines/medicines-human-use"),
            (f"DailyMed ({inn_lower})", f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?query={enc}"),
            (f"BNF (NICE) {inn_lower} dosing", f"https://bnf.nice.org.uk/search/?q={enc}"),
        )
    return [
        SourceCandidate(
            id_type="URL",
            id=url,
            url=url,
            title=title,
            year=None,
            journal=None,
        )
        for title, url in items
    ]


class PubMedClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self.cache = get_cache(config.cache_dir)
        self.base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    def _common_params(self) -> Dict[str, str]:
        params: Dict[str, str] = {"tool": self.config.ncbi_tool}
        if self.config.ncbi_email:
            params["email"] = self.config.ncbi_email
        if self.config.ncbi_api_key:
            params["api_key"] = self.config.ncbi_api_key
        return params

    def _throttle(self) -> None:
        """Задержка перед запросом к NCBI: без API-ключа лимит 3 req/s, с ключом — 10 req/s."""
        if not self.config.ncbi_api_key:
            time.sleep(0.35)
        else:
            time.sleep(0.11)

    def _esearch(self, db: str, term: str, retmax: int) -> List[str]:
        # NCBI E-utilities ESearch (no scraping).
        self._throttle()
        url = f"{self.base_url}esearch.fcgi"
        params = {
            "db": db,
            "term": term,
            "retmax": retmax,
            "retmode": "json",
        }
        params.update(self._common_params())
        data = request_json_with_cache(self.cache, url, params)
        return data.get("esearchresult", {}).get("idlist", [])

    def _esummary(self, db: str, ids: List[str]) -> Dict[str, Dict[str, str]]:
        if not ids:
            return {}
        self._throttle()
        # ESummary returns metadata (title, journal, pubdate).
        url = f"{self.base_url}esummary.fcgi"
        params = {
            "db": db,
            "id": ",".join(ids),
            "retmode": "json",
        }
        params.update(self._common_params())
        data = request_json_with_cache(self.cache, url, params)
        result = {}
        for uid, item in data.get("result", {}).items():
            if uid == "uids":
                continue
            result[uid] = item
        return result

    def resolve_sources(
        self, ref_ids: List[str], inn: str
    ) -> Tuple[List[SourceCandidate], List[str]]:
        """Resolve ref_ids (PMID/PMCID/URL) to SourceCandidates. Used when selected_sources override.
        Returns (sources, warnings). Does not filter by noise (user chose these explicitly)."""
        warnings: List[str] = []
        sources: List[SourceCandidate] = []
        seen: set = set()

        pubmed_ids: List[str] = []
        pmc_ids: List[str] = []
        ref_order: List[Tuple[str, str, str]] = []  # (id_type, id_val, ref_id)

        for ref in ref_ids:
            id_type, id_val, norm_ref = _parse_ref_id(ref)
            if not id_type or norm_ref in seen:
                continue
            seen.add(norm_ref)
            if id_type == "PMID":
                pubmed_ids.append(id_val)
                ref_order.append(("PMID", id_val, norm_ref))
            elif id_type == "PMCID":
                clean = id_val.lstrip("PMC")
                pmc_ids.append(clean)
                ref_order.append(("PMCID", clean, norm_ref))
            elif id_type == "URL":
                ref_order.append(("URL", id_val, norm_ref))

        pubmed_summary = self._esummary("pubmed", pubmed_ids) if pubmed_ids else {}
        pmc_summary = self._esummary("pmc", pmc_ids) if pmc_ids else {}

        for ref_type, id_val, norm_ref in ref_order:
            if ref_type == "PMID":
                item = pubmed_summary.get(id_val)
                if not item:
                    warnings.append(f"PMID:{id_val} not found in NCBI.")
                    continue
                title = normalize_space(item.get("title", ""))
                pubdate = item.get("pubdate", "")
                year = self._extract_year(pubdate)
                journal = normalize_space(item.get("fulljournalname") or item.get("source") or "") or None
                sources.append(
                    SourceCandidate(
                        id_type="PMID",
                        id=id_val,
                        url=f"https://pubmed.ncbi.nlm.nih.gov/{id_val}/",
                        title=title or f"PubMed {id_val}",
                        year=int(year) if year else None,
                        journal=journal,
                        type_tags=self._infer_type_tags(title),
                        species=self._infer_species(title),
                        feeding=self._infer_feeding(title),
                    )
                )
            elif ref_type == "PMCID":
                item = pmc_summary.get(id_val) or pmc_summary.get(f"PMC{id_val}")
                if not item:
                    warnings.append(f"PMCID:{id_val} not found in NCBI.")
                    continue
                title = normalize_space(item.get("title", ""))
                pubdate = item.get("pubdate", "")
                year = self._extract_year(pubdate)
                journal = normalize_space(item.get("fulljournalname") or item.get("source") or "") or None
                sources.append(
                    SourceCandidate(
                        id_type="PMCID",
                        id=id_val,
                        url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{id_val}/",
                        title=title or f"PMC {id_val}",
                        year=int(year) if year else None,
                        journal=journal,
                        type_tags=self._infer_type_tags(title),
                        species=self._infer_species(title),
                        feeding=self._infer_feeding(title),
                    )
                )
            elif ref_type == "URL":
                try:
                    from urllib.parse import urlparse
                    host = urlparse(id_val).netloc or "official"
                    title = f"Official source ({host})"
                except Exception:
                    title = "Official source (URL)"
                sources.append(
                    SourceCandidate(
                        id_type="URL",
                        id=id_val,
                        url=id_val,
                        title=title,
                        year=None,
                        journal=None,
                    )
                )

        return sources, warnings

    def get_official_sources(self, inn: str) -> List[SourceCandidate]:
        """Return official/regulatory URL sources for the given INN."""
        return _get_official_sources(inn)

    def _build_pubmed_query_step_a(self, inn: str) -> str:
        """Step A: high precision — INN in title or as MeSH major topic."""
        inn_esc = inn.strip()
        return (
            f"(({inn_esc}[ti] OR {inn_esc}[majr]) AND "
            f"({_THEMATIC_TERMS}) AND {_HUMANS_ONLY} AND "
            f"NOT ({_ANTI_TERMS}))"
        )

    def _build_pubmed_query_step_b(self, inn: str) -> str:
        """Step B: expansion — INN in title/abstract."""
        inn_esc = inn.strip()
        return (
            f"(({inn_esc}[tiab]) AND "
            f"({_THEMATIC_TERMS}) AND {_HUMANS_ONLY} AND "
            f"NOT ({_ANTI_TERMS}))"
        )

    def _build_pmc_query(self, inn: str) -> str:
        """PMC: title/abstract + thematic + anti (PMC has no [mh] species filter)."""
        inn_esc = inn.strip()
        return (
            f"({inn_esc}[Title/Abstract]) AND "
            f"({_PMC_THEMATIC}) AND "
            f"NOT ({_PMC_ANTI})"
        )

    def search_sources(
        self, inn: str, retmax: int = 10, mode: str = "be"
    ) -> Tuple[str, List[SourceCandidate], List[str]]:
        warnings: List[str] = []
        sources: List[SourceCandidate] = []
        seen_ref_ids: set = set()
        seen_title_year: set = set()
        used_query = ""

        def _dedupe_add(candidate: SourceCandidate) -> None:
            if candidate.ref_id in seen_ref_ids:
                return
            key = (normalize_space(candidate.title).lower()[:120], candidate.year)
            if key in seen_title_year:
                return
            seen_ref_ids.add(candidate.ref_id)
            seen_title_year.add(key)
            if self._is_noise_title(candidate.title, allow_ddi=(mode == "ddi")):
                return
            sources.append(candidate)

        inn_clean = (inn or "").strip()
        if not inn_clean:
            warnings.append("INN is empty.")
            return "", sources, warnings

        # Step A (high precision): INN in title or MeSH major
        query_a = self._build_pubmed_query_step_a(inn_clean)
        pubmed_ids = self._esearch("pubmed", query_a, retmax)
        used_query = query_a

        # Step B (expansion) if Step A returned too few
        if len(pubmed_ids) < _MIN_RESULTS_STEP_A:
            query_b = self._build_pubmed_query_step_b(inn_clean)
            ids_b = self._esearch("pubmed", query_b, retmax)
            seen_pmid = set(pubmed_ids)
            for pid in ids_b:
                if pid not in seen_pmid:
                    pubmed_ids.append(pid)
                    seen_pmid.add(pid)
            used_query = query_b
            if ids_b:
                warnings.append("Step B (title/abstract) was used to expand results.")

        if not pubmed_ids:
            warnings.append("No PubMed records found (Step A and B).")

        pubmed_summary = self._esummary("pubmed", pubmed_ids)
        for pmid, item in pubmed_summary.items():
            title = normalize_space(item.get("title", ""))
            pubdate = item.get("pubdate", "")
            year = self._extract_year(pubdate)
            type_tags = self._infer_type_tags(title)
            species = self._infer_species(title)
            feeding = self._infer_feeding(title)
            journal = normalize_space(item.get("fulljournalname") or item.get("source") or "")
            _dedupe_add(
                SourceCandidate(
                    id_type="PMID",
                    id=str(pmid),
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    title=title,
                    year=int(year) if year else None,
                    journal=journal or None,
                    type_tags=type_tags,
                    species=species,
                    feeding=feeding,
                )
            )

        # PMC: single query (title/abstract + thematic + anti)
        pmc_query = self._build_pmc_query(inn_clean)
        pmc_ids = self._esearch("pmc", pmc_query, retmax)
        if not pubmed_ids and not pmc_ids:
            warnings.append("No PubMed/PMC records found via E-utilities.")

        pmc_summary = self._esummary("pmc", pmc_ids)
        for pmcid, item in pmc_summary.items():
            title = normalize_space(item.get("title", ""))
            pubdate = item.get("pubdate", "")
            year = self._extract_year(pubdate)
            type_tags = self._infer_type_tags(title)
            species = self._infer_species(title)
            feeding = self._infer_feeding(title)
            if self._is_noise_title(title, allow_ddi=(mode == "ddi")):
                continue
            key = (title.lower()[:120], int(year) if year else None)
            if key in seen_title_year:
                continue
            cand = SourceCandidate(
                id_type="PMCID",
                id=str(pmcid).lstrip("PMC"),
                url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/",
                title=title,
                year=int(year) if year else None,
                journal=normalize_space(item.get("fulljournalname") or item.get("source") or "") or None,
                type_tags=type_tags,
                species=species,
                feeding=feeding,
            )
            if cand.ref_id in seen_ref_ids:
                continue
            seen_ref_ids.add(cand.ref_id)
            seen_title_year.add(key)
            sources.append(cand)

        # Rank: fetch abstracts, score, filter by threshold, sort (score desc, year desc)
        if sources:
            ref_ids = [s.ref_id for s in sources]
            abstracts_map = self.fetch_abstracts(ref_ids)
            scored: List[Tuple[int, SourceCandidate]] = []
            for s in sources:
                abstract = abstracts_map.get(s.ref_id) or ""
                sc = self._score_source(s.title, abstract, inn_clean, s.species)
                if sc >= _SCORE_THRESHOLD:
                    scored.append((sc, s))
            scored.sort(key=lambda x: (-x[0], -(x[1].year or 0)))
            sources = [cand for _, cand in scored]

        # Official/regulatory sources (always appended; id_type=URL)
        sources.extend(_get_official_sources(inn_clean))

        return used_query, sources, warnings

    @staticmethod
    def _is_noise_title(title: str, *, allow_ddi: bool = False) -> bool:
        """Exclude DDI, phenotyping, cocktail/probe, microdose, veterinary (post-filter backup).
        When allow_ddi=True (mode=ddi), drug interaction terms are not treated as noise."""
        t = (title or "").lower()
        noise = [
            "phenotyping",
            "phenotype",
            "cocktail",
            "probe drug",
            "probe drugs",
            "microdose",
            "veterinary",
            " in rats",
            " in mice",
            " in dogs",
            " in pigs",
            " in horse",
            "rat ",
            "mouse ",
            "canine",
            "feline",
            "equine",
        ]
        if not allow_ddi:
            noise.extend(("drug-drug interaction", "drug interaction", " ddi "))
        return any(n in t for n in noise)

    @staticmethod
    def _score_source(
        title: str,
        abstract: str,
        inn: str,
        species: Optional[str],
    ) -> int:
        """Score a source: plus for INN/theme/must, minus for anti/other-drug/animal. Returns total."""
        t = (title or "").lower()
        a = (abstract or "").lower()
        inn_l = (inn or "").strip().lower()
        score = 0

        # +10 INN in title (exact token)
        if inn_l:
            token_re = re.compile(r"\b" + re.escape(inn_l) + r"\b", re.I)
            if token_re.search(t):
                score += 10
            # +5 INN in title (partial/variant)
            elif inn_l in t:
                score += 5

        # +3 per theme keyword in title, +1 in abstract
        for kw in _THEME_KEYWORDS:
            if kw in t:
                score += 3
            if kw in a:
                score += 1

        # +2 if any must-keyword (delayed-release/enteric/dissolution)
        for kw in _MUST_KEYWORDS:
            if kw in t or kw in a:
                score += 2
                break

        # -10 anti in title
        for kw in _ANTI_KEYWORDS:
            if kw in t:
                score -= 10
                break
        # -5 anti only in abstract (if not already -10 from title)
        if not any(kw in t for kw in _ANTI_KEYWORDS):
            for kw in _ANTI_KEYWORDS:
                if kw in a:
                    score -= 5
                    break

        # -10 title like "<other drug> ... effect of <inn> ..." (INN as modifier)
        if inn_l and "pharmacokinetics" in t and re.search(
            r"effect(s)?\s+of\s+" + re.escape(inn_l), t, re.I
        ):
            score -= 10

        # -20 animal study (once)
        animal_markers = (" in rats", " in mice", " in dogs", "veterinary", " in healthy horses")
        if species == "animal" or any(m in t for m in animal_markers) or any(m in a for m in animal_markers):
            score -= 20

        return score

    def fetch_abstracts(self, ids: List[str]) -> Dict[str, str]:
        # Accept ref_id: PMID:123 or PMCID:123 (or legacy numeric / PMCID:x)
        pubmed_ids: List[str] = []
        pmc_ids: List[str] = []
        for i in ids:
            if not i:
                continue
            s = i.strip()
            if s.upper().startswith("PMCID:"):
                pmc_ids.append(s.split(":", 1)[1].strip().lstrip("PMC"))
            elif s.upper().startswith("PMID:"):
                pubmed_ids.append(s.split(":", 1)[1].strip())
            elif s.upper().startswith("URL:"):
                continue  # official/regulatory URLs: no abstract from NCBI
            elif s.isdigit():
                pubmed_ids.append(s)
            else:
                pubmed_ids.append(s)
        # PMC ids from API are numeric; strip PMC prefix if present
        pmc_ids = [x.lstrip("PMC") for x in pmc_ids]

        abstracts: Dict[str, str] = {}
        if pubmed_ids:
            raw_pubmed = self._efetch_abstracts("pubmed", pubmed_ids)
            for pid, text in raw_pubmed.items():
                abstracts[f"PMID:{pid}"] = text
        if pmc_ids:
            pmc_abstracts = self._efetch_abstracts("pmc", pmc_ids)
            for pmcid, text in pmc_abstracts.items():
                # PMC XML may return numeric or PMC-prefixed id
                n = str(pmcid).lstrip("PMC")
                abstracts[f"PMCID:{n}"] = text
        return abstracts

    def _efetch_abstracts(self, db: str, ids: List[str]) -> Dict[str, str]:
        if not ids:
            return {}
        self._throttle()
        # EFetch returns abstracts/full records in XML.
        # Для PubMed abstract возвращается при rettype=abstract.
        # Для PMC без rettype=full NCBI отдаёт DocSum/Medline — тегов <article>/<abstract> нет.
        url = f"{self.base_url}efetch.fcgi"
        params = {
            "db": db,
            "id": ",".join(ids),
            "retmode": "xml",
        }
        if db == "pubmed":
            params["rettype"] = "abstract"
        elif db == "pmc":
            params["rettype"] = "full"  # JATS XML с <article> и <abstract>
        params.update(self._common_params())
        text = request_text_with_cache(self.cache, url, params)
        return self._parse_abstracts_xml(text)

    def _parse_abstracts_xml(self, xml_text: str) -> Dict[str, str]:
        abstracts: Dict[str, str] = {}
        try:
            root = ElementTree.fromstring(xml_text)
        except Exception:
            return abstracts

        for article in root.findall(".//PubmedArticle"):
            pmid_node = article.find(".//PMID")
            abstract_nodes = article.findall(".//AbstractText")
            if pmid_node is None:
                continue
            pmid = pmid_node.text or ""
            abstract = " ".join([normalize_space(n.text or "") for n in abstract_nodes])
            abstracts[pmid] = abstract

        for article in root.findall(".//article"):
            # PMC XML includes <article-id pub-id-type="pmc">PMCID</article-id>
            pmc_id_node = article.find(".//article-id[@pub-id-type='pmc']")
            abstract_nodes = article.findall(".//abstract//p")
            if pmc_id_node is None:
                continue
            pmc_id = pmc_id_node.text or ""
            abstract = " ".join([normalize_space(n.text or "") for n in abstract_nodes])
            abstracts[pmc_id] = abstract

        return abstracts

    @staticmethod
    def _extract_year(pubdate: str) -> str | None:
        if not pubdate:
            return None
        match = re.search(r"(19|20)\d{2}", pubdate)
        return match.group(0) if match else None

    @staticmethod
    def _infer_type_tags(title: str) -> List[str]:
        title_l = (title or "").lower()
        tags: List[str] = []
        if "bioequivalence" in title_l or "bioequivalent" in title_l:
            tags.append("BE")
        if "pharmacokinetic" in title_l or "pharmacokinetics" in title_l:
            tags.append("PK")
        if "review" in title_l:
            tags.append("review")
        return tags

    @staticmethod
    def _infer_species(title: str) -> Optional[str]:
        title_l = (title or "").lower()
        if "rat" in title_l or "mouse" in title_l or "animal" in title_l:
            return "animal"
        if "human" in title_l or "healthy" in title_l:
            return "human"
        return None

    @staticmethod
    def _infer_feeding(title: str) -> Optional[str]:
        title_l = (title or "").lower()
        if "fasted" in title_l:
            return "fasted"
        if "fed" in title_l:
            return "fed"
        return None
