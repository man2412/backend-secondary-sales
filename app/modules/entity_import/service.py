"""
Entity-import orchestrator.

Five-phase ingestion:

    1. Stockists       — upsert by normalized name (insert if missing)
    2. Headquarters    — LOOKUP only (existing master data); warn on missing
    3. Medical stores  — group sheet rows by fuzzy key + DB-side dedup with
                         alternate_names merging; insert/match per group
    4. Doctors         — group by normalized name + HQ; insert/match
    5. MR ↔ Doctor allocations — resolve FSO name → MR user, upsert pairs

The flow is idempotent: re-running on the same sheet inserts nothing new
(matches existing rows and refreshes is_active where applicable).

Transaction model: the caller's session.commit() wraps the whole import.
A single malformed row never aborts the run — failures are captured per-row
in the summary, processing continues.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doctor import Doctor
from app.models.master import Headquarter
from app.models.stockist import MedicalStore, Stockist
from app.models.user import User
from app.modules.entity_import.normalize import (
    canonical_city_from_location,
    clean_whitespace,
    extract_city_from_store_raw,
    is_fuzzy_match,
    normalize_doctor_name,
    normalize_hq_key,
    normalize_store_core,
    normalize_store_name,
    normalize_stockist_key,
    normalize_stockist_name,
    pick_canonical_store_name,
    store_name_tokens,
)
from app.modules.entity_import.parser import ImportRow
from app.modules.entity_import.repository import EntityImportRepository
from app.modules.entity_import.schemas import (
    EntityImportCounts,
    EntityImportFailure,
    EntityImportSummary,
    EntityImportWarning,
    StoreMergeRecord,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal row models built as the pipeline runs
# ---------------------------------------------------------------------------


@dataclass
class _StoreGroup:
    """One in-sheet group of chemist-name variants that all map to one store."""

    key: tuple  # (stockist_id, hq_id, name_tokens frozenset)
    name_tokens: frozenset[str]
    core_token: str
    city_token: str
    stockist_id: uuid.UUID | None
    headquarter_id: uuid.UUID | None
    variants: list[str]
    addresses: list[str]
    row_indexes: list[int]
    # Filled in during resolution:
    canonical_name: str = ""
    medical_store_id: uuid.UUID | None = None
    matched_existing: bool = False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EntityImportService:
    def __init__(self, repo: EntityImportRepository | None = None) -> None:
        self._repo = repo or EntityImportRepository()

    async def ingest(
        self,
        db: AsyncSession,
        rows: list[ImportRow],
        *,
        filename: str,
        uploaded_by: uuid.UUID,
        default_state_id: uuid.UUID | None = None,
        default_division_ids: list[uuid.UUID] | None = None,
    ) -> EntityImportSummary:
        t_total = time.perf_counter()
        prefix = f"[entity-import file={filename!r}]"
        summary = EntityImportSummary(
            filename=filename,
            total_rows=len(rows),
            processed_rows=0,
            skipped_rows=0,
            stockists=EntityImportCounts(),
            headquarters=EntityImportCounts(),
            medical_stores=EntityImportCounts(),
            doctors=EntityImportCounts(),
            doctor_store_links=EntityImportCounts(),
            mr_doctor_allocations=EntityImportCounts(),
            mr_headquarter_allocations=EntityImportCounts(),
        )

        if not rows:
            logger.warning("%s no rows to ingest", prefix)
            summary.elapsed_ms = (time.perf_counter() - t_total) * 1000
            return summary

        logger.info("%s starting ingestion rows=%d", prefix, len(rows))

        # ------------------------------------------------------------------
        # Phase 1: Stockists
        # ------------------------------------------------------------------
        t = time.perf_counter()
        stockist_by_key: dict[str, Stockist] = await self._resolve_stockists(
            db, rows, summary
        )
        logger.info(
            "%s phase1 stockists: distinct=%d inserted=%d existing=%d elapsed_ms=%.0f",
            prefix, len(stockist_by_key),
            summary.stockists.inserted, summary.stockists.matched_existing,
            (time.perf_counter() - t) * 1000,
        )

        # ------------------------------------------------------------------
        # Phase 2: Headquarters (lookup + optional insert when defaults given)
        # ------------------------------------------------------------------
        t = time.perf_counter()
        hq_by_key: dict[str, Headquarter] = await self._resolve_headquarters(
            db, rows, summary,
            default_state_id=default_state_id,
            default_division_ids=default_division_ids,
        )
        logger.info(
            "%s phase2 headquarters: distinct=%d matched_existing=%d inserted=%d missing=%d elapsed_ms=%.0f",
            prefix, len(hq_by_key),
            summary.headquarters.matched_existing,
            summary.headquarters.inserted,
            sum(1 for w in summary.warnings if w.kind == "missing_headquarter"),
            (time.perf_counter() - t) * 1000,
        )

        # ------------------------------------------------------------------
        # Phase 3: Medical stores (in-sheet group + DB dedup + alternate names)
        # ------------------------------------------------------------------
        t = time.perf_counter()
        store_groups, row_to_group = self._group_store_rows(
            rows, stockist_by_key, hq_by_key
        )
        await self._resolve_store_groups(db, store_groups, summary)
        logger.info(
            "%s phase3 stores: groups=%d inserted=%d matched_existing=%d merged_variants=%d "
            "elapsed_ms=%.0f",
            prefix, len(store_groups),
            summary.medical_stores.inserted,
            summary.medical_stores.matched_existing,
            summary.medical_stores.merged_duplicates,
            (time.perf_counter() - t) * 1000,
        )

        # ------------------------------------------------------------------
        # Phase 4: Doctors
        # ------------------------------------------------------------------
        t = time.perf_counter()
        doctor_groups, row_to_doctor_key = self._group_doctor_rows(rows, hq_by_key)
        doctor_by_key = await self._resolve_doctor_groups(
            db, doctor_groups, summary
        )
        logger.info(
            "%s phase4 doctors: groups=%d inserted=%d matched_existing=%d elapsed_ms=%.0f",
            prefix, len(doctor_groups),
            summary.doctors.inserted, summary.doctors.matched_existing,
            (time.perf_counter() - t) * 1000,
        )

        # ------------------------------------------------------------------
        # Phase 5a: Doctor ↔ MedicalStore links (idempotent bulk insert)
        # ------------------------------------------------------------------
        t = time.perf_counter()
        await self._link_doctors_to_stores(
            db, rows, doctor_by_key, row_to_doctor_key,
            store_groups, row_to_group, summary,
        )
        logger.info(
            "%s phase5a doc_store_links: created=%d existing=%d elapsed_ms=%.0f",
            prefix, summary.doctor_store_links.inserted,
            summary.doctor_store_links.matched_existing,
            (time.perf_counter() - t) * 1000,
        )

        # ------------------------------------------------------------------
        # Phase 5b: MR ↔ Doctor allocations
        # ------------------------------------------------------------------
        t = time.perf_counter()
        await self._allocate_mrs_to_doctors(
            db, rows, doctor_by_key, row_to_doctor_key,
            uploaded_by=uploaded_by, summary=summary,
        )
        logger.info(
            "%s phase5b mr_doctor_allocs: created=%d existing=%d | "
            "phase5c mr_hq_allocs: created=%d existing=%d elapsed_ms=%.0f",
            prefix, summary.mr_doctor_allocations.inserted,
            summary.mr_doctor_allocations.matched_existing,
            summary.mr_headquarter_allocations.inserted,
            summary.mr_headquarter_allocations.matched_existing,
            (time.perf_counter() - t) * 1000,
        )

        # ------------------------------------------------------------------
        # Final tallies
        # ------------------------------------------------------------------
        summary.skipped_rows = len(
            {f.row_index for f in summary.failures}
            | {w.row_index for w in summary.warnings if w.row_index is not None}
        )
        summary.processed_rows = summary.total_rows - summary.skipped_rows
        summary.elapsed_ms = (time.perf_counter() - t_total) * 1000

        logger.info(
            "%s done processed=%d/%d skipped=%d warnings=%d failures=%d total_ms=%.0f",
            prefix, summary.processed_rows, summary.total_rows, summary.skipped_rows,
            len(summary.warnings), len(summary.failures), summary.elapsed_ms,
        )
        return summary

    # ======================================================================
    # Phase 1: Stockists
    # ======================================================================

    async def _resolve_stockists(
        self,
        db: AsyncSession,
        rows: list[ImportRow],
        summary: EntityImportSummary,
    ) -> dict[str, Stockist]:
        """
        For each distinct stockist on the sheet, ensure a row exists. Match
        existing rows by case-insensitive normalized name; insert otherwise.
        Returns a `normalized_key → Stockist` map for the rest of the run.
        """
        # Build distinct (key, clean_name) pairs from the sheet.
        sheet_stockists: dict[str, str] = {}  # key → clean_name
        for r in rows:
            key = normalize_stockist_key(r.stockist_name)
            if not key:
                continue
            clean_name, _div_label = normalize_stockist_name(r.stockist_name)
            sheet_stockists.setdefault(key, clean_name)

        # Pull every active stockist once (the table is small — bulk read is cheap).
        existing = await self._repo.list_active_stockists(db)
        existing_by_key: dict[str, Stockist] = {}
        for s in existing:
            key = normalize_stockist_key(s.name)
            if key:
                existing_by_key.setdefault(key, s)

        out: dict[str, Stockist] = {}
        for key, clean_name in sheet_stockists.items():
            existing_row = existing_by_key.get(key)
            if existing_row is not None:
                out[key] = existing_row
                summary.stockists.matched_existing += 1
                continue
            try:
                new_row = await self._repo.insert_stockist(db, name=clean_name)
                out[key] = new_row
                existing_by_key[key] = new_row
                summary.stockists.inserted += 1
            except Exception as exc:
                logger.exception("phase1: failed to insert stockist %r", clean_name)
                summary.warnings.append(
                    EntityImportWarning(
                        kind="stockist_insert_failed",
                        message=f"Failed to insert stockist {clean_name!r}: {exc}",
                    )
                )
        return out

    # ======================================================================
    # Phase 2: Headquarters (lookup-only)
    # ======================================================================

    async def _resolve_headquarters(
        self,
        db: AsyncSession,
        rows: list[ImportRow],
        summary: EntityImportSummary,
        *,
        default_state_id: uuid.UUID | None = None,
        default_division_ids: list[uuid.UUID] | None = None,
    ) -> dict[str, Headquarter]:
        """
        Look up Headquarters by name (case-insensitive).

        Behaviour for HQs the sheet references but master data is missing:
          * if `default_state_id` AND `default_division_ids` were supplied
            on the API call, insert the missing HQ with those defaults; or
          * otherwise record a 'missing_headquarter' warning so rows tied
            to it skip the HQ-dependent inserts.
        """
        names = {clean_whitespace(r.headquarter) for r in rows if r.headquarter}
        if not names:
            return {}
        existing = await self._repo.list_headquarters_by_names(db, names)
        by_key: dict[str, Headquarter] = {}
        for hq in existing:
            by_key[normalize_hq_key(hq.name)] = hq

        can_autoinsert = bool(default_state_id and default_division_ids)

        for name in names:
            key = normalize_hq_key(name)
            if key in by_key:
                summary.headquarters.matched_existing += 1
                continue
            if can_autoinsert:
                try:
                    new_hq = await self._repo.insert_headquarter(
                        db,
                        name=name,
                        state_id=default_state_id,  # type: ignore[arg-type]
                        division_ids=default_division_ids or [],
                    )
                    by_key[key] = new_hq
                    summary.headquarters.inserted += 1
                except Exception as exc:
                    logger.exception("phase2: failed to insert headquarter %r", name)
                    summary.warnings.append(
                        EntityImportWarning(
                            kind="missing_headquarter",
                            message=(
                                f"Failed to auto-insert headquarter {name!r}: {exc}. "
                                "Rows referencing it will skip store/doctor inserts."
                            ),
                        )
                    )
                continue
            summary.warnings.append(
                EntityImportWarning(
                    kind="missing_headquarter",
                    message=(
                        f"Headquarter {name!r} not found in master data — "
                        "rows referencing it will skip store/doctor inserts. "
                        "Pass `state_id` and `division_ids` to the upload API "
                        "to auto-create missing HQs, or add this HQ via the "
                        "master API before re-running."
                    ),
                )
            )
        return by_key

    # ======================================================================
    # Phase 3: Medical stores
    # ======================================================================

    def _group_store_rows(
        self,
        rows: list[ImportRow],
        stockist_by_key: dict[str, Stockist],
        hq_by_key: dict[str, Headquarter],
    ) -> tuple[list[_StoreGroup], dict[int, tuple]]:
        """
        Group every sheet row's chemist name into in-sheet duplicate clusters.

        Strategy:
          1. Build a token set for each chemist name (city aliases applied,
             stopwords kept since they DO differentiate stores).
          2. Initial group key = (stockist_id, headquarter_id, frozenset(tokens))
             — identical normalized names collapse trivially.
          3. Fuzzy-merge within each (stockist, hq) bucket: if one group's
             token set is a *subset* of another (e.g. variant w/o city is
             contained in the variant that includes the city), merge them.
             This also catches small fuzzy core matches when the smaller set
             is at least 2 tokens and shares ≥80% of its tokens.

        Two stores in the same (stockist, hq) that share only brand tokens
        but differ in store-type ('CHEMIST' vs 'MEDICAL STORE') correctly
        stay separate because the type token is part of the set.
        """
        # Pass 1: assemble per-row data
        per_row: list[
            tuple[int, str, frozenset[str], str, str, str, uuid.UUID, uuid.UUID]
        ] = []  # (row_index, raw, tokens, core, city, address, sid, hid)
        for r in rows:
            if not r.chemist_name:
                continue
            stockist = stockist_by_key.get(normalize_stockist_key(r.stockist_name))
            hq = hq_by_key.get(normalize_hq_key(r.headquarter))
            if stockist is None or hq is None:
                # Skip silently — store inserts require both. Downstream
                # phases (doctors, allocs) still run with whatever they have.
                continue
            tokens = store_name_tokens(r.chemist_name)
            if not tokens:
                continue
            core = normalize_store_core(r.chemist_name)
            city = (
                canonical_city_from_location(r.chemist_location)
                or extract_city_from_store_raw(r.chemist_name)
                or canonical_city_from_location(r.doctor_location)
            )
            addr = clean_whitespace(r.chemist_location) or clean_whitespace(r.doctor_address)
            per_row.append(
                (r.row_index, r.chemist_name, tokens, core, city, addr, stockist.id, hq.id)
            )

        # Pass 2: initial grouping by exact (stockist, hq, frozenset(tokens))
        groups: dict[tuple[uuid.UUID, uuid.UUID, frozenset[str]], _StoreGroup] = {}
        row_to_group: dict[int, tuple] = {}

        for row_idx, raw, tokens, core, city, addr, sid, hid in per_row:
            key = (sid, hid, tokens)
            grp = groups.get(key)
            if grp is None:
                grp = _StoreGroup(
                    key=key,
                    name_tokens=tokens,
                    core_token=core,
                    city_token=city,
                    stockist_id=sid,
                    headquarter_id=hid,
                    variants=[],
                    addresses=[],
                    row_indexes=[],
                )
                groups[key] = grp
            grp.variants.append(raw)
            if addr:
                grp.addresses.append(addr)
            grp.row_indexes.append(row_idx)
            row_to_group[row_idx] = key

        # Pass 3: fuzzy-merge by token-set subset, scoped by (stockist, hq)
        by_sh: dict[tuple[uuid.UUID, uuid.UUID], list[_StoreGroup]] = defaultdict(list)
        for g in groups.values():
            by_sh[(g.stockist_id, g.headquarter_id)].append(g)

        final_groups: list[_StoreGroup] = []
        for (_sid, _hid), bucket in by_sh.items():
            # Sort largest-token-set first so smaller subsets find a target.
            bucket.sort(key=lambda g: len(g.name_tokens), reverse=True)
            survivors: list[_StoreGroup] = []
            for grp in bucket:
                hit: _StoreGroup | None = None
                for s in survivors:
                    if self._store_tokens_compatible(grp.name_tokens, s.name_tokens):
                        hit = s
                        break
                if hit is None:
                    survivors.append(grp)
                else:
                    hit.variants.extend(grp.variants)
                    hit.addresses.extend(grp.addresses)
                    hit.row_indexes.extend(grp.row_indexes)
                    for ri in grp.row_indexes:
                        row_to_group[ri] = hit.key
            final_groups.extend(survivors)

        # Final cleanup: dedupe variants/addresses, pick canonical primary name.
        for g in final_groups:
            g.variants = list(dict.fromkeys([v for v in g.variants if v]))
            g.addresses = list(dict.fromkeys([a for a in g.addresses if a]))
            g.canonical_name = pick_canonical_store_name(g.variants)

        return final_groups, row_to_group

    @staticmethod
    def _store_tokens_compatible(a: frozenset[str], b: frozenset[str]) -> bool:
        """
        Decide whether two store-name token sets refer to the same store.

        Returns True iff:
          * one set is a subset of the other (handles 'with city' vs 'no city'
            variants like 'SHYAM MEDICINES AHMEDABAD' inside
            'SHYAM MEDICINES SATELLITE AHMEDABAD'), AND
          * the smaller set has ≥2 identifying tokens so a 1-word coincidence
            ('AMBICA' alone) never collapses two distinct stores.

        Two unrelated stores in the same stockist/HQ scope ('AMBICA MEDICAL
        STORE SATELLITE' vs 'AMBICA CHEMIST VEJALPUR') have neither set as a
        subset of the other → they stay separate.
        """
        if not a or not b:
            return False
        if a == b:
            return True
        smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
        if len(smaller) < 2:
            return False
        return smaller.issubset(larger)

    async def _resolve_store_groups(
        self,
        db: AsyncSession,
        groups: list[_StoreGroup],
        summary: EntityImportSummary,
    ) -> None:
        """
        For each in-sheet store group, find an existing MedicalStore (same
        stockist, same HQ) whose canonical name *or any alternate_name*
        fuzzy-matches; otherwise INSERT a new store. Existing stores get
        their `alternate_names` array unioned with new variants.
        """
        if not groups:
            return

        stockist_ids = {g.stockist_id for g in groups if g.stockist_id is not None}
        existing_stores = await self._repo.list_stores_for_stockists(db, stockist_ids)

        # Index existing stores by (stockist_id, hq_id) for narrow per-group scans.
        by_scope: dict[tuple[uuid.UUID, uuid.UUID | None], list[MedicalStore]] = defaultdict(list)
        for s in existing_stores:
            by_scope[(s.stockist_id, s.headquarter_id)].append(s)

        for g in groups:
            scope = by_scope.get((g.stockist_id, g.headquarter_id), [])
            match = self._find_existing_store_match(g, scope)

            if match is not None:
                g.medical_store_id = match.id
                g.matched_existing = True
                # union the new variants into alternate_names
                new_alts = [v for v in g.variants if v and v != match.name]
                await self._repo.update_store_alternates(
                    db, match, new_alternates=new_alts,
                    new_name=g.canonical_name if not match.name else None,
                )
                summary.medical_stores.matched_existing += 1
                if len(g.variants) > 1:
                    summary.medical_stores.merged_duplicates += 1
            else:
                # New store: primary name from canonical pick, alternates from the rest.
                primary = g.canonical_name or (g.variants[0] if g.variants else "")
                alternates = [v for v in g.variants if v and v != primary]
                address = g.addresses[0] if g.addresses else None
                try:
                    new_store = await self._repo.insert_medical_store(
                        db,
                        name=primary,
                        stockist_id=g.stockist_id,
                        headquarter_id=g.headquarter_id,
                        alternate_names=alternates,
                        address=address,
                    )
                    g.medical_store_id = new_store.id
                    by_scope[(g.stockist_id, g.headquarter_id)].append(new_store)
                    summary.medical_stores.inserted += 1
                    if len(g.variants) > 1:
                        summary.medical_stores.merged_duplicates += 1
                except Exception as exc:
                    logger.exception("phase3: failed to insert store %r", primary)
                    summary.failures.append(
                        EntityImportFailure(
                            row_index=g.row_indexes[0] if g.row_indexes else 0,
                            message=f"Failed to insert medical store {primary!r}: {exc}",
                        )
                    )
                    continue

            summary.merged_store_groups.append(
                StoreMergeRecord(
                    canonical_name=g.canonical_name or "",
                    variants=g.variants,
                    medical_store_id=g.medical_store_id,
                    matched_existing=g.matched_existing,
                )
            )

    @classmethod
    def _find_existing_store_match(
        cls, group: _StoreGroup, candidates: list[MedicalStore]
    ) -> MedicalStore | None:
        """
        Pick the existing DB store that represents the same real-world store
        as `group`. Reuses `_store_tokens_compatible` so the rules match the
        in-sheet grouping exactly.
        """
        if not candidates:
            return None

        # Collect every token-set view of the group: from each variant individually,
        # plus the merged set we computed during grouping.
        target_sets: list[frozenset[str]] = [group.name_tokens]
        for v in group.variants:
            ts = store_name_tokens(v)
            if ts and ts not in target_sets:
                target_sets.append(ts)

        # Build per-candidate token sets across primary name + alternates.
        for cand in candidates:
            cand_sets: list[frozenset[str]] = []
            nk = store_name_tokens(cand.name)
            if nk:
                cand_sets.append(nk)
            for alt in cand.alternate_names or []:
                ak = store_name_tokens(alt)
                if ak and ak not in cand_sets:
                    cand_sets.append(ak)
            for tk in target_sets:
                for ck in cand_sets:
                    if cls._store_tokens_compatible(tk, ck):
                        return cand
        return None

    # ======================================================================
    # Phase 4: Doctors
    # ======================================================================

    def _group_doctor_rows(
        self,
        rows: list[ImportRow],
        hq_by_key: dict[str, Headquarter],
    ) -> tuple[dict[tuple[str, uuid.UUID | None], dict], dict[int, tuple[str, uuid.UUID | None]]]:
        """
        Group rows by `(normalized_doctor_name, headquarter_id)`. Within each
        group we collect the longest seen address/phone for the canonical
        doctor record.
        """
        groups: dict[tuple[str, uuid.UUID | None], dict] = {}
        row_to_key: dict[int, tuple[str, uuid.UUID | None]] = {}

        for r in rows:
            doctor_key = normalize_doctor_name(r.doctor_name)
            if not doctor_key:
                continue
            hq = hq_by_key.get(normalize_hq_key(r.headquarter))
            hq_id = hq.id if hq is not None else None
            key = (doctor_key, hq_id)

            g = groups.get(key)
            if g is None:
                g = {
                    "normalized": doctor_key,
                    "display_name": clean_whitespace(r.doctor_name),
                    "hq_id": hq_id,
                    "address_choices": [],
                    "phone": clean_whitespace(r.contact) or None,
                    "specialty": clean_whitespace(r.specialty) or None,
                    "degree": clean_whitespace(r.degree) or None,
                    "row_indexes": [],
                }
                groups[key] = g
            # accumulate evidence
            addr = clean_whitespace(r.doctor_address) or clean_whitespace(r.doctor_location)
            if addr:
                g["address_choices"].append(addr)
            if not g["phone"] and r.contact:
                g["phone"] = clean_whitespace(r.contact)
            if not g["specialty"] and r.specialty:
                g["specialty"] = clean_whitespace(r.specialty)
            if not g["degree"] and r.degree:
                g["degree"] = clean_whitespace(r.degree)
            g["row_indexes"].append(r.row_index)
            row_to_key[r.row_index] = key

        return groups, row_to_key

    async def _resolve_doctor_groups(
        self,
        db: AsyncSession,
        groups: dict[tuple[str, uuid.UUID | None], dict],
        summary: EntityImportSummary,
    ) -> dict[tuple[str, uuid.UUID | None], Doctor]:
        """
        For each (doctor_key, hq_id) group, find an existing Doctor row and
        re-use it, or INSERT a new one. Existing doctors without an HQ get
        their `headquarter_id` set when the sheet supplies one.
        """
        if not groups:
            return {}

        hq_ids = {key[1] for key in groups.keys() if key[1] is not None}
        existing_with_hq = await self._repo.list_doctors_by_hq(db, hq_ids)
        existing_no_hq = await self._repo.list_doctors_with_null_hq(db)

        # Index: full_name_norm → list of Doctors (across all hq buckets)
        by_norm: dict[str, list[Doctor]] = defaultdict(list)
        for d in (*existing_with_hq, *existing_no_hq):
            k = normalize_doctor_name(d.full_name)
            if k:
                by_norm[k].append(d)

        resolved: dict[tuple[str, uuid.UUID | None], Doctor] = {}
        for key, info in groups.items():
            doctor_key, hq_id = key
            existing = by_norm.get(doctor_key, [])

            # Prefer exact hq match; fall back to an existing doctor with NULL hq.
            chosen: Doctor | None = None
            for d in existing:
                if d.headquarter_id == hq_id and hq_id is not None:
                    chosen = d
                    break
            if chosen is None:
                for d in existing:
                    if d.headquarter_id is None:
                        chosen = d
                        break
            if chosen is None and existing:
                chosen = existing[0]  # any same-name match across HQs

            if chosen is not None:
                # Promote NULL hq → sheet's hq when we now know it.
                if hq_id is not None and chosen.headquarter_id is None:
                    await self._repo.update_doctor_hq(db, chosen, hq_id)
                resolved[key] = chosen
                summary.doctors.matched_existing += 1
                continue

            # Insert new
            try:
                addr = info["address_choices"][0] if info["address_choices"] else None
                new_doc = await self._repo.insert_doctor(
                    db,
                    full_name=info["display_name"] or doctor_key,
                    headquarter_id=hq_id,
                    phone=info.get("phone"),
                    address=addr,
                    specialization=info.get("specialty"),
                    qualification=info.get("degree"),
                )
                resolved[key] = new_doc
                by_norm[doctor_key].append(new_doc)
                summary.doctors.inserted += 1
            except Exception as exc:
                logger.exception("phase4: failed to insert doctor %r", info["display_name"])
                summary.failures.append(
                    EntityImportFailure(
                        row_index=info["row_indexes"][0] if info["row_indexes"] else 0,
                        message=f"Failed to insert doctor {info['display_name']!r}: {exc}",
                    )
                )
        return resolved

    # ======================================================================
    # Phase 5a: Doctor ↔ MedicalStore links
    # ======================================================================

    async def _link_doctors_to_stores(
        self,
        db: AsyncSession,
        rows: list[ImportRow],
        doctor_by_key: dict[tuple[str, uuid.UUID | None], Doctor],
        row_to_doctor_key: dict[int, tuple[str, uuid.UUID | None]],
        store_groups: list[_StoreGroup],
        row_to_store_group: dict[int, tuple],
        summary: EntityImportSummary,
    ) -> None:
        """Bulk insert (doctor_id, medical_store_id) pairs with ON CONFLICT DO NOTHING."""
        store_id_by_group_key: dict[tuple, uuid.UUID | None] = {
            g.key: g.medical_store_id for g in store_groups
        }

        pairs_to_insert: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for r in rows:
            doctor_key = row_to_doctor_key.get(r.row_index)
            store_group_key = row_to_store_group.get(r.row_index)
            if doctor_key is None or store_group_key is None:
                continue
            doctor = doctor_by_key.get(doctor_key)
            store_id = store_id_by_group_key.get(store_group_key)
            if doctor is None or store_id is None:
                continue
            pairs_to_insert.add((doctor.id, store_id))

        if not pairs_to_insert:
            return

        # Find which pairs already exist (so we report accurate created/existing counts).
        doctor_ids = {p[0] for p in pairs_to_insert}
        existing_pairs = await self._repo.list_existing_doctor_store_links(db, doctor_ids)
        new_pairs = pairs_to_insert - existing_pairs

        existing_in_run = pairs_to_insert & existing_pairs
        summary.doctor_store_links.matched_existing = len(existing_in_run)

        if new_pairs:
            inserted = await self._repo.bulk_insert_doctor_store_links(db, new_pairs)
            summary.doctor_store_links.inserted = inserted

    # ======================================================================
    # Phase 5b: MR ↔ Doctor allocations
    # ======================================================================

    async def _allocate_mrs_to_doctors(
        self,
        db: AsyncSession,
        rows: list[ImportRow],
        doctor_by_key: dict[tuple[str, uuid.UUID | None], Doctor],
        row_to_doctor_key: dict[int, tuple[str, uuid.UUID | None]],
        *,
        uploaded_by: uuid.UUID,
        summary: EntityImportSummary,
    ) -> None:
        """
        Resolve FSO NAME → MR user (case-insensitive full_name match) and
        upsert MrDoctorAllocation. If an FSO name matches multiple MR users
        (or none), the row is logged as a warning and skipped.
        """
        # Build name→[users] index from all active MRs.
        # Use only whitespace normalisation + uppercase — digits and punctuation
        # like hyphens are preserved so that "RAJKOT-1" and "RAJKOT-2" stay
        # distinct and never collapse into each other.
        def _fso_key(s: str | None) -> str:
            return clean_whitespace(s).upper() if s else ""

        mrs = await self._repo.list_mr_users(db)
        mr_by_norm: dict[str, list[User]] = defaultdict(list)
        for u in mrs:
            mr_by_norm[_fso_key(u.full_name)].append(u)

        # Build (mr_id, doctor_id) pairs we want active. Also build the set of
        # (mr_id, headquarter_id) allocations implied by each matched doctor's HQ
        # — allocating a doctor to an MR also gives that MR a presence in the
        # doctor's headquarter, which downstream sales import needs to resolve
        # HQ/state/division.
        wanted: set[tuple[uuid.UUID, uuid.UUID]] = set()
        wanted_hq: set[tuple[uuid.UUID, uuid.UUID]] = set()
        # Track warned-names so we don't emit the same warning per-row.
        warned_unknown: set[str] = set()
        warned_ambiguous: set[str] = set()
        # Track row→warning so the summary's skipped_rows count is meaningful.
        for r in rows:
            doctor_key = row_to_doctor_key.get(r.row_index)
            doctor = doctor_by_key.get(doctor_key) if doctor_key else None
            if doctor is None:
                continue
            fso_norm = _fso_key(r.fso_name)
            if not fso_norm:
                continue
            candidates = mr_by_norm.get(fso_norm, [])
            if not candidates:
                # Auto-create a placeholder MR user (random email/supabase_id) so
                # the allocation proceeds instead of being skipped. The new user
                # is cached so later rows with the same FSO reuse it.
                try:
                    new_mr = await self._repo.insert_mr_user(db, full_name=r.fso_name or fso_norm)
                except Exception as exc:
                    logger.exception(
                        "phase5b: failed to auto-create MR user for %r", r.fso_name
                    )
                    if fso_norm not in warned_unknown:
                        summary.warnings.append(
                            EntityImportWarning(
                                row_index=r.row_index,
                                kind="unknown_mr",
                                message=(
                                    f"FSO/MR {r.fso_name!r} not found and auto-create "
                                    f"failed ({exc}) — doctor allocation skipped."
                                ),
                            )
                        )
                        warned_unknown.add(fso_norm)
                    continue
                mr_by_norm[fso_norm] = [new_mr]
                candidates = [new_mr]
                if fso_norm not in warned_unknown:
                    # No row_index → informational only; does not mark rows skipped.
                    summary.warnings.append(
                        EntityImportWarning(
                            kind="created_mr",
                            message=(
                                f"FSO/MR {r.fso_name!r} had no matching user — created a "
                                f"placeholder MR ({new_mr.email}). Set its real email / "
                                "details and headquarter via the user admin later."
                            ),
                        )
                    )
                    warned_unknown.add(fso_norm)
            if len(candidates) > 1:
                if fso_norm not in warned_ambiguous:
                    names = ", ".join(sorted({c.email for c in candidates}))
                    summary.warnings.append(
                        EntityImportWarning(
                            row_index=r.row_index,
                            kind="ambiguous_mr",
                            message=(
                                f"FSO/MR {r.fso_name!r} matches multiple users ({names}) — "
                                "doctor allocation skipped."
                            ),
                        )
                    )
                    warned_ambiguous.add(fso_norm)
                continue
            mr_id = candidates[0].id
            wanted.add((mr_id, doctor.id))
            if doctor.headquarter_id is not None:
                wanted_hq.add((mr_id, doctor.headquarter_id))

        if not wanted:
            return

        # Find which pairs already exist so we report accurate counts.
        existing_map = await self._repo.list_existing_mr_doctor_pairs(
            db, {p[0] for p in wanted}
        )
        new_pairs = [p for p in wanted if p not in existing_map]
        refreshed_pairs = [p for p in wanted if p in existing_map]
        summary.mr_doctor_allocations.matched_existing = len(refreshed_pairs)

        # The bulk_upsert handles both new + existing (ON CONFLICT DO UPDATE).
        upsert_rows = [
            {
                "id": uuid.uuid4(),
                "mr_id": mid,
                "doctor_id": did,
                "allocated_by": uploaded_by,
                "is_active": True,
            }
            for mid, did in wanted
        ]
        await self._repo.bulk_upsert_mr_doctor_allocations(db, rows=upsert_rows)
        summary.mr_doctor_allocations.inserted = len(new_pairs)

        # ------------------------------------------------------------------
        # Phase 5c: MR ↔ Headquarter allocations (derived from doctors' HQs)
        # ------------------------------------------------------------------
        if not wanted_hq:
            return
        existing_hq = await self._repo.list_existing_mr_hq_pairs(
            db, {p[0] for p in wanted_hq}
        )
        new_hq_pairs = [p for p in wanted_hq if p not in existing_hq]
        summary.mr_headquarter_allocations.matched_existing = (
            len(wanted_hq) - len(new_hq_pairs)
        )
        if new_hq_pairs:
            hq_rows = [
                {
                    "id": uuid.uuid4(),
                    "mr_id": mid,
                    "headquarter_id": hid,
                    "allocated_by": uploaded_by,
                    "is_active": True,
                }
                for mid, hid in new_hq_pairs
            ]
            await self._repo.bulk_insert_mr_headquarter_allocations(db, rows=hq_rows)
            summary.mr_headquarter_allocations.inserted = len(new_hq_pairs)
