"""Store scraped data in Supabase (table `scraped_pages`, see schema.sql)."""
import os


def _client():
    from supabase import create_client

    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def save_to_supabase(record: dict) -> None:
    """Insert a snapshot row; if the record has an `id` (a queued batch row) fill that row in."""
    table = _client().table("scraped_pages")
    if "id" in record:
        row = {k: v for k, v in record.items() if k != "id"}
        table.update(row).eq("id", record["id"]).execute()
    else:
        table.insert(record).execute()


def queue_batch(batch_id: str, urls: list) -> list:
    """Create one 'queued' row per URL; returns [{"id", "url"}, ...]."""
    from scraper import domain_of

    rows = [{"url": u, "domain": domain_of(u), "status": "queued", "batch_id": batch_id} for u in urls]
    return _client().table("scraped_pages").insert(rows).execute().data


def get_batch(batch_id: str) -> list:
    res = (
        _client()
        .table("scraped_pages")
        .select("url,domain,title,status,error,signals")
        .eq("batch_id", batch_id)
        .order("id")
        .execute()
    )
    return res.data
