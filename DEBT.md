# Technical and product debt

Written at the end of phase 1 — the warehouse catalogue is loaded and the site
is deployable. Everything here is a known, deliberate gap, not a defect to
chase. Each item says what it costs you today, so you can sequence phase 2 on
business impact rather than on what sounds most urgent.

Figures are from the live import: **483 products, 493 source rows, 7,798 units.**

---

## P0 — blocks revenue

### 1. There are no prices. Not one.

**Today:** every SKU is sell-on-request. A buyer can find a part, confirm it's
in stock, and ask what it costs — then waits for a human. Nothing can be bought
without your sales team in the loop.

**Why:** the warehouse workbook has no price column. It tracks stock, not
commercial terms.

**Cost:** every enquiry consumes sales time, including the ones that would have
converted unattended. You also can't do anything price-driven — no sorting by
price, no budget filter, no "from X" on a category tile.

**Phase 2 work:** decide where price lives. Three realistic options:

| Option | Fits if | Effort |
|---|---|---|
| A price column in the same workbook | Pricing already exists in a spreadsheet somewhere | Small — the importer already has the column wired, it just reads empty |
| Enter prices in the admin panel | Pricing is decided per-part by a person | None to build; it's data entry across 483 SKUs |
| Cost + margin rule | You price off a landed cost | Medium — needs a cost field and a margin table |

The schema is already ready for all three: `product_variants.price` is nullable,
`variant_prices` holds per-currency amounts, and the admin form accepts both.

**Partial is fine.** Price your top 50 movers and the rest stay on request. The
storefront already handles a mixed catalogue — priced items show a price, the
rest say "on request".

### 2. Nothing is emailed

**Today:** a quote request lands in the admin queue and nowhere else. If nobody
opens the admin panel, nobody knows it arrived. The buyer gets a reference
number on screen and silence afterwards.

**Cost:** this is the most likely way to lose a real order in the current build.

**Phase 2 work:** connect a mail provider. Two messages carry almost all the
value — "we got your request" to the buyer, and "a request arrived" to sales.
Half a day with any transactional provider. The storefront already tells people
no automatic email is sent, so this is a gap you've disclosed, not one you're
hiding.

---

## P1 — makes the catalogue harder to sell from

### 3. 183 of 483 products have no brand (38%)

The brand was inferred from the part name and note. Where the text didn't name
a manufacturer, the product shows as **Unbranded** — which is wrong for nearly
all of them, since an HPE part number implies HPE.

**Cost:** the brand facet is the second-most-used filter on a parts catalogue,
and it's unreliable for over a third of stock.

**Fix:** part-number prefixes are highly predictable (`P…-B21`, `8…-B21` → HPE;
`0B…` → HGST; and so on). A prefix table would resolve most of the 183
automatically. Half a day, and it improves every re-import afterwards.

### 4. 101 products have no part number

These got a generated SKU (`NOPN-…`) so they could be imported at all. They're
mostly CPUs and memory, where the workbook records a model (`6426Y`,
`32GB 2R 3200 HPE`) rather than a part number.

**Cost:** a buyer searching for the manufacturer part number won't find them,
and a generated SKU is meaningless on a quote or an invoice.

**Fix:** this is a data job, not a code one — the part numbers exist on the
boxes. Until then they are findable by name and spec.

### 5. Product names are warehouse shorthand

71 products have names under 12 characters: `BAY`, `300 15K HP`, `6TB 12G SAS`.
They make sense to someone standing in the warehouse and to nobody else.

**Cost:** these are the page titles and search results a customer sees. They
also hurt search ranking, because there's little text to match against.

**Fix:** compose a display name from the structured fields the importer already
extracted — `HPE 300GB 15K SAS 2.5"` instead of `300 15K HP`. The specs are
parsed; it's a naming rule, roughly a day.

### 6. No descriptions, no photography

All 483 products have an empty description and no image. The storefront falls
back to a category silhouette, which looks deliberate rather than broken, but
it is not a photograph of the part you're selling.

**Cost:** lower conversion, no differentiation against a competitor who shows
the item.

**Fix:** photograph the top sellers first. Upload works today in the product
form. Descriptions could be generated from the spec fields as a starting point.

---

## P2 — operational

### 7. Currency rates are typed in by hand

Three currencies are live (IRR base, USD, AED) but nothing refreshes the rates.
A stale rate silently misprices every *converted* figure.

Note the blast radius is small by design: a price entered directly against a
currency is always shown as entered. Conversion only fills gaps.

**Fix:** either a scheduled rate fetch, or price your main SKUs in all three
currencies and never convert. The second is more work up front and more correct
commercially.

### 8. Stock is one number, but there are two warehouses

The workbook tracks انبارA (395 parts) and انبار B (70). The import keeps the
location as a spec field, so you can see it, but stock is a single figure per
SKU and the site can't answer "what's in Warehouse B".

**Fix:** stock per location. A real schema change — a `stock_locations` table
and a per-location quantity. Worth doing only when fulfilment actually splits
across the two.

### 9. Serial numbers aren't modelled

The workbook has a Serial Numbers sheet (4 rows used) and a stock-movement sheet
(empty). Neither is imported. For warranty claims and RMA on used server parts,
serial tracking is normally the thing that matters.

**Fix:** sizeable — a serial table, intake/dispatch flows, and a UI. Only worth
it if you actually handle RMAs by serial today.

### 10. Rate limiting doesn't survive more than one worker

The contact and quote throttles count requests in process memory. Production
runs 4 workers, so the real limit is four times what the code says.

**Fix:** move the counter to Redis. An hour. Do it before the forms get abused,
not after.

### 11. 7 duplicate part numbers were merged on import

Seven SKUs appeared twice in the workbook with different quantities. The
importer added them together and logged each one. That's the sensible default,
but if the duplicates were actually different parts, the merge is wrong.

**Fix:** check the import warnings against the workbook once. The importer
prints them on every run.

### 12. Six rows had unreadable quantities

Four were the header word `تعداد` pasted into the body; two were `10 ES` and
`2 ES` — ten and two engineering samples. The importer read the numbers and
flagged the condition. Worth fixing at source so the next import is clean.

---

### 13. Elasticsearch is a hard dependency for search

There is no second query path. If the ES cluster is unreachable,
`/api/products/search` and the category pages return **503**, the header
type-ahead returns an empty list, and `/health` reports `degraded`. Product
detail pages, the quote flow and the admin panel are unaffected — they read
Postgres directly.

This is deliberate: a Postgres fallback existed and was removed, because a
second implementation of "what does this query return" produced visibly
different answers from the primary one (measured: typo queries found nothing,
and two-word queries like `Samsung 16GB` returned 0 results against 2 from ES).
A wrong answer during an incident is worse than a clear error.

What that costs you, and what to do about it:

- **Run ES with a replica in production.** The local single-node setup runs with
  one unassigned replica (index health `yellow`); that is fine for development
  and not fine for a storefront.
- **Alert on `/health` reporting `degraded`,** not just on the API being up.
- **Reindex is manual recovery.** `POST /api/admin/products/reindex` rebuilds the
  index from Postgres. Nothing automatically detects a stale or partial index.
- **Unexplained drift, measured.** For the query `HPE`, ES reported 162 HPE
  products and category `hdd` = 62; a direct Postgres count of the same set gave
  162 + 1 `Unbranded` and `hdd` = 63. A one-product disagreement was never root
  caused. Worth chasing before launch — it points at either a stale document or
  a bug in how brand/category is denormalized at index time.

## Not debt — decisions

These look like gaps and aren't. They were chosen.

- **No customer accounts.** Checkout is guest-only; a reference tracks the
  request. Right for trade buyers who order occasionally.
- **No payment gateway.** You invoice. The quote flow is built around it.
- **Elasticsearch has no authentication.** It is not reachable outside the
  compose network. Adding auth to a private service buys nothing.
- **The importer never deletes.** A part dropped from the spreadsheet keeps its
  last known stock rather than vanishing from the catalogue mid-quote.
- **Digits stay Latin in Persian and Arabic.** Part numbers and prices are data
  a buyer cross-checks against a datasheet.

---

## Suggested phase 2 order

1. **Email on quote requests** (#2) — half a day, stops you losing orders
2. **Prices for the top sellers** (#1) — data entry, unblocks self-serve buying
3. **Brand from part-number prefix** (#3) — half a day, fixes the main filter
4. **Display names from specs** (#5) — a day, improves every listing and search
5. **Redis rate limiting** (#10) — an hour, before you need it

Everything below that can wait for evidence that it matters.
