# API Contract — APTUS API (single-tenant)

This backend runs in **single-tenant mode**. There is no organization partitioning field in the DB/API and no list endpoints accept such a query parameter.

Use `{BASE_URL}/openapi.json` and `{BASE_URL}/docs` as the canonical contract.

## Notes

- All list endpoints use `page`, `per_page`, optional `q`/filters, and `include_inactive` as implemented per route.
- Visibility rules depend on role and (where applicable) hierarchy/state.
- Secondary sales and reports are scoped by visible MR ids.

