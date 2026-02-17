from __future__ import annotations

import re
from typing import Dict, List, Tuple
from xml.etree import ElementTree

from backend.schemas import NumericValue, SourceRecord
from backend.services.utils import (
    AppConfig,
    get_cache,
    normalize_space,
    request_json_with_cache,
    request_text_with_cache,
)


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

    def _esearch(self, db: str, term: str, retmax: int) -> List[str]:
        # NCBI E-utilities ESearch (no scraping).
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

    def search_sources(self, inn: str, retmax: int = 10) -> Tuple[str, List[SourceRecord], List[str]]:
        query = f"{inn}[Title/Abstract] AND (pharmacokinetics OR bioavailability OR Cmax OR AUC)"
        warnings: List[str] = []
        sources: List[SourceRecord] = []

        pubmed_ids = self._esearch("pubmed", query, retmax)
        pmc_ids = self._esearch("pmc", query, retmax)

        if not pubmed_ids and not pmc_ids:
            warnings.append("No PubMed/PMC records found via E-utilities.")

        pubmed_summary = self._esummary("pubmed", pubmed_ids)
        for pmid, item in pubmed_summary.items():
            title = normalize_space(item.get("title", ""))
            pubdate = item.get("pubdate", "")
            year = self._extract_year(pubdate)
            year_val = None
            if year:
                year_val = NumericValue(
                    value=float(year),
                    unit="year",
                    evidence=[
                        {
                            "source_type": "URL",
                            "source": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                            "snippet": f"PubDate: {pubdate}",
                            "context": "NCBI ESummary",
                        }
                    ],
                )
            sources.append(
                SourceRecord(
                    pmid=str(pmid),
                    title=title,
                    year=year_val,
                    journal=item.get("source"),
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                )
            )

        pmc_summary = self._esummary("pmc", pmc_ids)
        for pmcid, item in pmc_summary.items():
            title = normalize_space(item.get("title", ""))
            pubdate = item.get("pubdate", "")
            year = self._extract_year(pubdate)
            year_val = None
            if year:
                year_val = NumericValue(
                    value=float(year),
                    unit="year",
                    evidence=[
                        {
                            "source_type": "URL",
                            "source": f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/",
                            "snippet": f"PubDate: {pubdate}",
                            "context": "NCBI ESummary",
                        }
                    ],
                )
            sources.append(
                SourceRecord(
                    pmid=f"PMCID:{pmcid}",
                    title=title,
                    year=year_val,
                    journal=item.get("source"),
                    url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/",
                )
            )

        return query, sources, warnings

    def fetch_abstracts(self, ids: List[str]) -> Dict[str, str]:
        pubmed_ids = [i for i in ids if not i.startswith("PMCID:")]
        pmc_ids = [i.replace("PMCID:", "") for i in ids if i.startswith("PMCID:")]

        abstracts: Dict[str, str] = {}
        if pubmed_ids:
            abstracts.update(self._efetch_abstracts("pubmed", pubmed_ids))
        if pmc_ids:
            pmc_abstracts = self._efetch_abstracts("pmc", pmc_ids)
            for pmcid, text in pmc_abstracts.items():
                abstracts[f"PMCID:{pmcid}"] = text
        return abstracts

    def _efetch_abstracts(self, db: str, ids: List[str]) -> Dict[str, str]:
        # EFetch returns abstracts/full records in XML.
        url = f"{self.base_url}efetch.fcgi"
        params = {
            "db": db,
            "id": ",".join(ids),
            "retmode": "xml",
        }
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
