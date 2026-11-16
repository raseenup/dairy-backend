"""Copy an existing Supabase product catalog into v8 without altering source tables.
Dry run by default. Prices must be reviewed as INR before --apply is used.
Historical accounts/orders require a separately reviewed mapping; this command
never silently converts or overwrites those records.
"""
from dotenv import load_dotenv
load_dotenv()
import argparse
from decimal import Decimal
import secure_backend as sec

def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--prices-are-inr',action='store_true')
    args=parser.parse_args()
    if not sec.DATABASE_URL:raise SystemExit('Set DATABASE_URL to the original Supabase PostgreSQL database.')
    with sec.postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.products')")
            if cur.fetchone()[0] is None:raise SystemExit('The existing public.products table was not found.')
            cur.execute('SELECT to_jsonb(p) FROM public.products AS p')
            records=[row[0] for row in cur.fetchall()]
    products=[]
    for row in records:
        ident=str(row.get('id') or '')
        if not ident:raise SystemExit('Every product must have an ID.')
        price=float(row.get('price') or 0);sec.money(price)
        quantity=int(row.get('stock_quantity') or 0)
        if quantity<0:raise SystemExit('Negative stock must be corrected before import.')
        products.append(dict(id=ident,name=sec.clean_text(row.get('name'),150),description=sec.clean_text(row.get('description')),
            price=price,unit=sec.clean_text(row.get('unit') or 'Unit',80),stock_quantity=quantity,
            min_stock_threshold=max(0,int(row.get('min_stock_threshold') or 0)),image_url=row.get('image_url') or '',
            currency='INR',tax_rate=float(row.get('tax_rate') or 0),version=1))
    print('Products found:',len(products))
    print('Source public tables will remain unchanged.')
    if not args.apply:
        print('Dry run only. Review INR prices and rerun with --apply --prices-are-inr.')
        return
    if not args.prices_are_inr:raise SystemExit('Confirm reviewed INR prices with --prices-are-inr.')
    with sec.transaction() as state:
        if state['orders']:raise SystemExit('Import must be reviewed separately once v8 orders exist.')
        for product in products:state['products'][product['id']]=product
        # Retain starter products without silently deleting them. Admin can remove unwanted entries.
        sec.audit(state,'backend-host','LEGACY_CATALOG_IMPORTED',str(len(products)))
    print('Catalog imported. Existing v8 starter entries were retained; review duplicates in Admin.')

if __name__=='__main__':run()
