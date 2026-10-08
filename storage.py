"""Store scraped data in Supabase (table `scraped_pages`, see schema.sql)."""
import os


def save_to_supabase(record: dict) -> None:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    client.table("scraped_pages").insert(record).execute()
