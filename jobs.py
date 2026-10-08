"""Run one scrape and persist the outcome (used by Modal workers and tests)."""
import logging

from fetch import FetchError
from scraper import domain_of, normalize_input

log = logging.getLogger(__name__)


def run_job(scrape, save, url, batch_id=None, row_id=None) -> dict:
    try:
        record = scrape(url)
    except (FetchError, ValueError) as e:
        record = _error(url, str(e))
    except Exception:
        log.exception("Unexpected failure scraping %s", url)
        record = _error(url, "Unexpected error while scraping")
    record["batch_id"] = batch_id
    if row_id is not None:
        record["id"] = row_id
    save(record)
    return record


def _error(url, message):
    try:
        domain = domain_of(normalize_input(url))
    except ValueError:
        domain = None
    return {"url": url, "domain": domain, "title": None, "links": [], "signals": {}, "status": "error", "error": message}
