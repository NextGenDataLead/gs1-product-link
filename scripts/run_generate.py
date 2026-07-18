"""Run the content generator's producer spine (generator SPEC, commit 4).

Usage:
    python -m scripts.run_generate CLIENT_ID [--products PATH] [--emit]
    python -m scripts.run_generate CLIENT_ID --ingest [--results PATH]

The spine is producer-agnostic: it prepares the pending copy-generation gaps and moves them
through the shared cache (``lib.generator``) without ever calling an LLM itself. It

1. deterministically fills the cache for products whose feature/benefit copy (attr 1067) is
   short enough to use verbatim (``prefill_from_feed``) — no producer needed;
2. computes the remaining gaps (``pending_requests``), each tagged tighten or generate; and
3. hands those gaps to a producer by one of two paths that write identical cache entries:

   - **emit / ingest** (default): ``--emit`` writes the pending requests to
     ``output/{client_id}/data/generation_requests.json`` for a Cowork session to fill, and
     ``--ingest`` reads that session's ``generation_results.json`` back into the cache.
   - **an injected** :class:`~lib.generator.LLMClient` (the headless API backend and test
     fakes) fills the cache directly through the same ``apply_result`` contract via
     :func:`run_producer`. The CLI wiring for that backend lands in a later commit.

``run_plan`` later only *reads* the cache. This pipeline fails silently — verify the emitted
requests and the cache against real parsed data, not just green tests.

Emits (--emit):   output/{client_id}/data/generation_requests.json
Reads (--ingest): output/{client_id}/data/generation_results.json (writes the cache)
Exit codes:
    0  success
    2  config error (bad client id, missing products/results file, malformed results file)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.config import get_client
from lib.errors import ConfigError, GeneratorError
from lib.generator import (
    DEFAULT_PROMPT_VERSION,
    MODE_TIGHTEN,
    ORIGIN_GENERATED,
    ORIGIN_TIGHTENED,
    GeneratedCache,
    GenerationRequest,
    GenerationResult,
    LLMClient,
    apply_result,
    load_cache,
    pending_requests,
    prefill_from_feed,
    save_cache,
)
from lib.records import ProductRecord

_log = logging.getLogger("scripts.run_generate")

REQUESTS_FILENAME: Final = "generation_requests.json"
RESULTS_FILENAME: Final = "generation_results.json"

#: Provenance recorded for cache entries filled from a Cowork session's results file.
_COWORK_PROVENANCE: Final = "cowork"

_EXIT_OK = 0
_EXIT_CONFIG_ERROR = 2


# --- Emit/ingest file contract -----------------------------------------------


class RequestsFile(BaseModel):
    """The emitted pending gaps for a Cowork session to fill (``--emit`` output).

    Carries the ``prompt_version`` and fingerprints so a session can echo them back in its
    results, letting ``--ingest`` reject copy generated against inputs that have since changed.
    """

    model_config = ConfigDict(frozen=True)

    client_id: str
    prompt_version: str
    generated_at: datetime
    requests: list[GenerationRequest]


class ResultItem(BaseModel):
    """One producer's answer, keyed to its ``(gtin, language)`` request.

    ``input_fingerprint`` is optional: when present it must match the pending request's, so a
    result generated against stale inputs is skipped rather than silently cached as fresh.
    """

    model_config = ConfigDict(frozen=True)

    gtin: str
    language: str
    usps: list[str] = Field(min_length=1)
    product_name: str | None = None
    input_fingerprint: str | None = None


class ResultsFile(BaseModel):
    """A Cowork session's generated copy, read back by ``--ingest``."""

    model_config = ConfigDict(frozen=True)

    client_id: str
    results: list[ResultItem]


# --- Paths & IO --------------------------------------------------------------


def _data_path(client_id: str, filename: str) -> Path:
    """Return ``output/{client_id}/data/{filename}``."""
    return Path("output") / client_id / "data" / filename


def _default_products_path(client_id: str) -> Path:
    """The parsed-products location written by ``scripts/parse_export.py``."""
    return _data_path(client_id, "products.json")


def _load_products(path: Path) -> list[ProductRecord]:
    """Read the parsed-products JSON array into ``ProductRecord``s."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ProductRecord.model_validate(item) for item in data]


def _load_results(path: Path, client_id: str) -> ResultsFile:
    """Read and validate a Cowork session's results file.

    Raises:
        GeneratorError: If the file names a different client than the run.
    """
    results = ResultsFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if results.client_id != client_id:
        raise GeneratorError(
            f"results file is for client {results.client_id!r}, not {client_id!r}"
        )
    return results


# --- Preparation & the producer seam -----------------------------------------


def _prepare(
    client_id: str, languages: list[str], products_path: Path, now: datetime
) -> tuple[GeneratedCache, list[GenerationRequest], list[ProductRecord]]:
    """Load products and cache, verbatim-prefill, and compute the pending gaps.

    Both emit and ingest run this so the cache always ends up with its verbatim entries,
    regardless of which is called first. Pure apart from reading the two input files.
    """
    products = _load_products(products_path)
    cache = load_cache(client_id)
    prefill_from_feed(products, cache, languages, DEFAULT_PROMPT_VERSION, now=now)
    requests = pending_requests(products, cache, languages, DEFAULT_PROMPT_VERSION)
    return cache, requests, products


def _origin_for_mode(mode: str) -> str:
    """Map a request mode to the cache-entry origin it produces."""
    return ORIGIN_TIGHTENED if mode == MODE_TIGHTEN else ORIGIN_GENERATED


def run_producer(
    cache: GeneratedCache,
    requests: list[GenerationRequest],
    client: LLMClient,
    *,
    provenance: str,
    now: datetime,
) -> int:
    """Drive ``client`` over every pending request, writing results into ``cache`` in place.

    The shared producer loop for any :class:`~lib.generator.LLMClient` — the API backend and
    test fakes alike. Does not persist; the caller saves the cache. Returns the count filled.
    """
    for request in requests:
        result = client.generate_copy(request)
        apply_result(
            cache,
            request,
            result,
            origin=_origin_for_mode(request.mode),
            provenance=provenance,
            now=now,
        )
    return len(requests)


# --- Emit / ingest -----------------------------------------------------------


def _emit(
    client_id: str, cache: GeneratedCache, requests: list[GenerationRequest], now: datetime
) -> Path:
    """Write the pending requests for a Cowork session and persist the verbatim prefill.

    Written **always**, even with no pending requests, so an empty ``requests`` list means
    "nothing to generate" rather than "no run has looked".
    """
    path = _data_path(client_id, REQUESTS_FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = RequestsFile(
        client_id=client_id,
        prompt_version=DEFAULT_PROMPT_VERSION,
        generated_at=now,
        requests=requests,
    )
    path.write_text(
        json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    save_cache(cache)
    return path


def _ingest(
    cache: GeneratedCache,
    requests: list[GenerationRequest],
    results: ResultsFile,
    now: datetime,
) -> tuple[int, int]:
    """Apply a session's results to ``cache`` in place and persist it.

    Matches each result to a pending request by ``(gtin, language)``. A result with no pending
    request (already fresh, or its input changed away) or a mismatched fingerprint (generated
    against stale inputs) is skipped with a warning rather than cached. Returns ``(applied,
    skipped)``.
    """
    by_key = {(r.gtin, r.language): r for r in requests}
    applied = 0
    skipped = 0
    for item in results.results:
        request = by_key.get((item.gtin, item.language))
        if request is None:
            _log.warning(
                "no pending request for %s/%s (already fresh or input changed); skipping",
                item.gtin,
                item.language,
            )
            skipped += 1
            continue
        stale = (
            item.input_fingerprint is not None
            and item.input_fingerprint != request.input_fingerprint
        )
        if stale:
            _log.warning(
                "stale result for %s/%s (fingerprint mismatch — inputs changed since emit); "
                "skipping",
                item.gtin,
                item.language,
            )
            skipped += 1
            continue
        apply_result(
            cache,
            request,
            GenerationResult(usps=item.usps, product_name=item.product_name),
            origin=_origin_for_mode(request.mode),
            provenance=_COWORK_PROVENANCE,
            now=now,
        )
        applied += 1
    save_cache(cache)
    return applied, skipped


# --- Summaries ---------------------------------------------------------------


def _coverage(total_units: int, requests: list[GenerationRequest]) -> str:
    """Render the cached-vs-pending coverage line (§ verify against real data)."""
    pending = len(requests)
    tighten = sum(1 for r in requests if r.mode == MODE_TIGHTEN)
    generate = pending - tighten
    covered = total_units - pending
    return (
        f"{covered}/{total_units} units cached; "
        f"{pending} pending ({tighten} tighten, {generate} generate)"
    )


# --- CLI ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_generate", description="Prepare and ingest generated product copy."
    )
    parser.add_argument("client_id", help="Key under clients: in clients.yml")
    parser.add_argument(
        "--products",
        help="Path to the parsed products JSON (default: output/{id}/data/products.json)",
    )
    parser.add_argument(
        "--results",
        help=(
            "Path to the results JSON to ingest "
            "(default: output/{id}/data/generation_results.json)"
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--emit",
        action="store_true",
        help="Write pending requests for a Cowork session (default)",
    )
    mode.add_argument(
        "--ingest",
        action="store_true",
        help="Read a session's results back into the cache",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    now = datetime.now(UTC)
    try:
        cfg = get_client(args.client_id)
        languages = cfg.wordpress.languages
        products_path = (
            Path(args.products) if args.products else _default_products_path(cfg.client_id)
        )
        cache, requests, products = _prepare(cfg.client_id, languages, products_path, now)
        total_units = len(products) * len(languages)

        if args.ingest:
            results_path = (
                Path(args.results) if args.results else _data_path(cfg.client_id, RESULTS_FILENAME)
            )
            results = _load_results(results_path, cfg.client_id)
            applied, skipped = _ingest(cache, requests, results, now)
            # Re-derive against the mutated cache so coverage reflects the post-ingest state,
            # not the gaps we started with.
            requests = pending_requests(products, cache, languages, DEFAULT_PROMPT_VERSION)
        else:
            emit_path = _emit(cfg.client_id, cache, requests, now)
    except (
        ConfigError,
        GeneratorError,
        FileNotFoundError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return _EXIT_CONFIG_ERROR

    coverage = _coverage(total_units, requests)
    if args.ingest:
        print(f"ingested {applied} result(s), skipped {skipped}; {coverage}", file=sys.stderr)
    else:
        _log.info("wrote %d request(s) to %s", len(requests), emit_path)
        print(f"emitted {len(requests)} request(s) to {emit_path}; {coverage}", file=sys.stderr)
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
