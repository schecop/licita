"""Parse one PLACSP ATOM page (CODICE extensions) into typed tenders.

The feed is a log of status updates, not a list of new tenders: the same tender (same ATOM entry
id) comes back each time it changes (PUB -> EV -> ADJ -> RES). The store keeps the newest version.
`ContractFolderID` is the buyer's own file number, only unique per buyer: it is not the key.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

ATOM = "{http://www.w3.org/2005/Atom}"
TOMB = "{http://purl.org/atompub/tombstones/1.0}"
CBC = "{urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2}"
CAC = "{urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2}"
CAC_PLACE = "{urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2}"
CBC_PLACE = "{urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2}"
MADRID = ZoneInfo("Europe/Madrid")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Document(_Frozen):
    kind: Literal["legal", "technical", "additional"]  # PCAP, PPT, anything else
    name: str
    url: str


class Lot(_Frozen):
    lot_id: str
    name: str | None
    budget_eur: Decimal | None  # without VAT
    cpvs: list[str]


class AwardCriterion(_Frozen):
    description: str
    weight: Decimal | None
    kind: str | None  # OBJ (formula) / SUBJ (judgement), as the feed codes it


class Tender(_Frozen):
    entry_id: str  # ATOM id: the stable key
    folder_id: str  # expediente, unique only per buyer
    updated: datetime
    status: str  # PRE, PUB, EV, ADJ, RES, ANUL
    title: str
    contract_type: (
        str | None
    )  # CODICE ContractCode: 1 supplies, 2 services, 3 works, 50 patrimonial
    buyer: str | None
    url: str | None  # human page on PLACSP
    budget_eur: Decimal | None  # whole contract, without VAT (presupuesto base de licitación)
    # valor estimado: budget plus extensions and modifications, without VAT. Solvency thresholds
    # in the pliegos usually refer to this, not to the budget. Default None: rows stored before
    # the field existed still load.
    estimated_value_eur: Decimal | None = None
    cpvs: list[str]
    nuts: str | None  # place of execution, NUTS code (ES51x = Cataluña)
    deadline: datetime | None  # tender submission deadline, Madrid time
    lots: list[Lot]
    documents: list[Document]
    award_criteria: list[AwardCriterion]


class FeedPage(_Frozen):
    tenders: list[Tender]
    deleted: list[str]  # entry ids of tombstoned entries
    next_url: str | None
    skipped: int = 0  # <entry> elements that did not parse into a Tender (counted, not hidden)

    @property
    def oldest(self) -> datetime | None:
        return min((t.updated for t in self.tenders), default=None)


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _cpvs(project: ET.Element | None) -> list[str]:
    if project is None:
        return []
    codes = project.iterfind(f"{CAC}RequiredCommodityClassification/{CBC}ItemClassificationCode")
    return [c.text.strip() for c in codes if c.text]


def _deadline(folder: ET.Element) -> datetime | None:
    period = folder.find(f"{CAC}TenderingProcess/{CAC}TenderSubmissionDeadlinePeriod")
    day = _text(period, f"{CBC}EndDate")
    if day is None:
        return None
    clock = _text(period, f"{CBC}EndTime") or "23:59:59"
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(clock), MADRID)


def _documents(folder: ET.Element) -> list[Document]:
    docs: list[Document] = []
    pairs: tuple[tuple[str, Literal["legal", "technical", "additional"]], ...] = (
        ("LegalDocumentReference", "legal"),
        ("TechnicalDocumentReference", "technical"),
        ("AdditionalDocumentReference", "additional"),
    )
    for tag, kind in pairs:
        for ref in folder.iterfind(f"{CAC}{tag}"):
            url = _text(ref, f"{CAC}Attachment/{CAC}ExternalReference/{CBC}URI")
            if url:
                docs.append(Document(kind=kind, name=_text(ref, f"{CBC}ID") or "", url=url))
    return docs


def _lots(folder: ET.Element) -> list[Lot]:
    lots = []
    for lot in folder.iterfind(f"{CAC}ProcurementProjectLot"):
        project = lot.find(f"{CAC}ProcurementProject")
        lots.append(
            Lot(
                lot_id=_text(lot, f"{CBC}ID") or "",
                name=_text(project, f"{CBC}Name"),
                budget_eur=_decimal(_text(project, f"{CAC}BudgetAmount/{CBC}TaxExclusiveAmount")),
                cpvs=_cpvs(project),
            )
        )
    return lots


def _award_criteria(folder: ET.Element) -> list[AwardCriterion]:
    path = f"{CAC}TenderingTerms/{CAC}AwardingTerms/{CAC}AwardingCriteria"
    return [
        AwardCriterion(
            description=_text(c, f"{CBC}Description") or "",
            weight=_decimal(_text(c, f"{CBC}WeightNumeric")),
            kind=_text(c, f"{CBC}AwardingCriteriaTypeCode"),
        )
        for c in folder.iterfind(path)
    ]


def _tender(entry: ET.Element) -> Tender | None:
    folder = entry.find(f"{CAC_PLACE}ContractFolderStatus")
    entry_id, updated = _text(entry, f"{ATOM}id"), _text(entry, f"{ATOM}updated")
    if folder is None or entry_id is None or updated is None:
        return None
    project = folder.find(f"{CAC}ProcurementProject")
    link = entry.find(f"{ATOM}link")
    return Tender(
        entry_id=entry_id,
        folder_id=_text(folder, f"{CBC}ContractFolderID") or "",
        updated=datetime.fromisoformat(updated),
        status=_text(folder, f"{CBC_PLACE}ContractFolderStatusCode") or "",
        title=_text(entry, f"{ATOM}title") or "",
        contract_type=_text(project, f"{CBC}TypeCode"),
        buyer=_text(
            folder, f"{CAC_PLACE}LocatedContractingParty/{CAC}Party/{CAC}PartyName/{CBC}Name"
        ),
        url=link.get("href") if link is not None else None,
        budget_eur=_decimal(_text(project, f"{CAC}BudgetAmount/{CBC}TaxExclusiveAmount")),
        estimated_value_eur=_decimal(
            _text(project, f"{CAC}BudgetAmount/{CBC}EstimatedOverallContractAmount")
        ),
        cpvs=_cpvs(project),
        nuts=_text(project, f"{CAC}RealizedLocation/{CBC}CountrySubentityCode"),
        deadline=_deadline(folder),
        lots=_lots(folder),
        documents=_documents(folder),
        award_criteria=_award_criteria(folder),
    )


def parse_page(xml: bytes) -> FeedPage:
    root = ET.fromstring(xml)
    entries = root.findall(f"{ATOM}entry")
    tenders = [t for e in entries if (t := _tender(e)) is not None]
    deleted = [ref for d in root.iterfind(f"{TOMB}deleted-entry") if (ref := d.get("ref"))]
    next_url = next(
        (link.get("href") for link in root.iterfind(f"{ATOM}link") if link.get("rel") == "next"),
        None,
    )
    return FeedPage(
        tenders=tenders, deleted=deleted, next_url=next_url, skipped=len(entries) - len(tenders)
    )
