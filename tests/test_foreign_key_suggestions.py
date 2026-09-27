from datasette.app import Datasette
from datasette.database import Database
from bs4 import BeautifulSoup as Soup
import itertools
import json
import pytest
import re

_memory_names = itertools.count(1)


def make_ds(extra_config=None, table_config=None):
    config = {"databases": {"data": {"tables": {}}}}
    if table_config:
        config["databases"]["data"]["tables"] = table_config
    if extra_config:
        config.update(extra_config)
    ds = Datasette([], config=config)
    return ds


def add_memory_db(ds):
    return ds.add_database(
        Database(ds, memory_name="test_fk_{}".format(next(_memory_names))),
        name="data",
    )


async def make_orders_ds(**kwargs):
    ds = make_ds(**kwargs)
    db = add_memory_db(ds)
    await db.execute_write_script("""
        create table customers (
            id integer primary key,
            name text,
            email text
        );
        create table orders (
            id integer primary key,
            customer_id integer references customers(id),
            total real
        );
        insert into customers (id, name, email) values
            (12, 'Alice', 'alice@example.com'),
            (112, 'Bob 50% off', 'bob@example.com'),
            (120, 'Carol_underscore', 'carol@example.com'),
            (5, 'Alice Other', 'alice2@example.com'),
            (7, '500 discount', 'five@example.com');
        insert into orders (id, customer_id, total) values (1, 12, 9.5);
    """)
    return ds


def suggestions_url(column="customer_id", q=None, limit=None):
    url = "/data/orders/-/foreign-key-suggestions?column={}".format(column)
    if q is not None:
        url += "&q={}".format(q)
    if limit is not None:
        url += "&limit={}".format(limit)
    return url


def table_data_from_html(html):
    match = re.search(
        r"window\._datasetteTableData\s*=\s*({.*?});", html, re.DOTALL
    )
    assert match is not None
    return json.loads(match.group(1))


@pytest.mark.asyncio
async def test_suggestions_exact_match_ranks_first():
    ds = await make_orders_ds()
    try:
        response = await ds.client.get(suggestions_url(q=12))
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        # Exact primary key match comes first, then other matches
        assert [row["value"] for row in data["rows"]] == [12, 112, 120]
        first = data["rows"][0]
        assert first["label"] == "Alice"
        assert first["url"] == "/data/customers/12"
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_label_search_and_types():
    ds = await make_orders_ds()
    try:
        response = await ds.client.get(suggestions_url(q="ali"))
        assert response.status_code == 200
        rows = response.json()["rows"]
        assert {row["value"] for row in rows} == {12, 5}
        # Integer primary keys stay integers in the JSON response
        assert all(isinstance(row["value"], int) for row in rows)
        labels = {row["value"]: row["label"] for row in rows}
        assert labels[12] == "Alice"
        assert labels[5] == "Alice Other"
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_escape_like_wildcards():
    ds = await make_orders_ds()
    try:
        # % in the query must be treated literally, not as a LIKE wildcard
        response = await ds.client.get(suggestions_url(q="50%25"))
        assert [row["value"] for row in response.json()["rows"]] == [112]
        # _ in the query must be treated literally too
        response = await ds.client.get(suggestions_url(q="carol_"))
        assert [row["value"] for row in response.json()["rows"]] == [120]
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_uses_configured_label_column():
    ds = await make_orders_ds(
        table_config={"customers": {"label_column": "email"}}
    )
    try:
        response = await ds.client.get(suggestions_url(q="alice@"))
        rows = response.json()["rows"]
        assert [row["value"] for row in rows] == [12]
        assert rows[0]["label"] == "alice@example.com"
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_label_falls_back_to_value():
    ds = make_ds()
    try:
        db = add_memory_db(ds)
        # No unique text column and no name/title column: no label column
        await db.execute_write_script("""
            create table things (id integer primary key, score integer, note text, extra text);
            create table refs (id integer primary key, thing_id integer references things(id));
            insert into things (id, score, note, extra) values (3, 10, 'x', 'y');
        """)
        response = await ds.client.get(
            "/data/refs/-/foreign-key-suggestions?column=thing_id&q=3"
        )
        assert response.status_code == 200
        assert response.json()["rows"] == [
            {"value": 3, "label": "3", "url": "/data/things/3"}
        ]
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_text_primary_key():
    ds = make_ds()
    try:
        db = add_memory_db(ds)
        await db.execute_write_script("""
            create table authors (username text primary key, full_name text);
            create table posts (
                id integer primary key,
                author text references authors(username)
            );
            insert into authors (username, full_name) values
                ('cleo', 'Cleo Sanchez'),
                ('cleopatra', 'Cleopatra Jones');
        """)
        response = await ds.client.get(
            "/data/posts/-/foreign-key-suggestions?column=author&q=cleo"
        )
        assert response.status_code == 200
        rows = response.json()["rows"]
        # Exact text match ranks first, values stay strings. The username
        # column is the label column Datasette detects for this table.
        assert rows[0] == {
            "value": "cleo",
            "label": "cleo",
            "url": "/data/authors/cleo",
        }
        assert {row["value"] for row in rows} == {"cleo", "cleopatra"}
        assert all(isinstance(row["value"], str) for row in rows)
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_empty_q_returns_no_rows():
    ds = await make_orders_ds()
    try:
        for url in (
            suggestions_url(),
            suggestions_url(q=""),
        ):
            response = await ds.client.get(url)
            assert response.status_code == 200
            assert response.json() == {"ok": True, "rows": []}
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_limit_parameter():
    ds = await make_orders_ds()
    try:
        response = await ds.client.get(suggestions_url(q="alice", limit=1))
        assert response.status_code == 200
        assert len(response.json()["rows"]) == 1
        for bad_limit in ("0", "101", "not-a-number"):
            response = await ds.client.get(
                suggestions_url(q="alice", limit=bad_limit)
            )
            assert response.status_code == 400
            assert response.json()["ok"] is False
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_errors():
    ds = await make_orders_ds()
    try:
        # Missing column
        response = await ds.client.get(
            "/data/orders/-/foreign-key-suggestions?q=12"
        )
        assert response.status_code == 400
        assert response.json()["ok"] is False
        # Column that is not a foreign key
        response = await ds.client.get(suggestions_url(column="total", q=1))
        assert response.status_code == 400
        assert "not a foreign key" in response.json()["errors"][0]
        # Unknown column
        response = await ds.client.get(suggestions_url(column="nope", q=1))
        assert response.status_code == 400
        # Unknown table
        response = await ds.client.get(
            "/data/nope/-/foreign-key-suggestions?column=x&q=1"
        )
        assert response.status_code == 404
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_compound_foreign_key_not_supported():
    ds = make_ds()
    try:
        db = add_memory_db(ds)
        await db.execute_write_script("""
            create table parents (a integer, b integer, primary key (a, b));
            create table children (
                id integer primary key,
                a integer,
                b integer,
                foreign key (a, b) references parents(a, b)
            );
        """)
        response = await ds.client.get(
            "/data/children/-/foreign-key-suggestions?column=a&q=1"
        )
        assert response.status_code == 400
        assert response.json()["ok"] is False
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_requires_view_on_referenced_table():
    ds = await make_orders_ds(
        table_config={
            "orders": {
                "permissions": {
                    "insert-row": {"id": "clerk"},
                    "update-row": {"id": "clerk"},
                },
            },
            "customers": {"permissions": {"view-table": {"id": "root"}}},
        }
    )
    try:
        # The clerk can insert and update orders but cannot view customers
        for actor in ({"id": "clerk"}, None):
            response = await ds.client.get(suggestions_url(q="ali"), actor=actor)
            assert response.status_code == 403
            assert response.json()["ok"] is False
            # Nothing from the customers table leaks
            assert "Alice" not in response.text
            assert "alice@example.com" not in response.text
        # root can view customers and gets suggestions
        response = await ds.client.get(suggestions_url(q="ali"), actor={"id": "root"})
        assert response.status_code == 200
        assert len(response.json()["rows"]) == 2
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_requires_view_on_source_table():
    ds = await make_orders_ds(
        table_config={"orders": {"permissions": {"view-table": {"id": "root"}}}}
    )
    try:
        response = await ds.client.get(suggestions_url(q="ali"), actor={"id": "clerk"})
        assert response.status_code == 403
        response = await ds.client.get(suggestions_url(q="ali"), actor={"id": "root"})
        assert response.status_code == 200
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_table_page_foreign_keys_data():
    ds = await make_orders_ds(
        table_config={
            "orders": {
                "permissions": {
                    "insert-row": {"id": "clerk"},
                    "update-row": {"id": "clerk"},
                },
            },
            "customers": {"permissions": {"view-table": {"id": "root"}}},
        }
    )
    try:
        # root can view customers: the table page advertises autocomplete
        response = await ds.client.get("/data/orders", actor={"id": "root"})
        assert response.status_code == 200
        table_data = table_data_from_html(response.text)
        assert table_data["foreignKeys"] == {
            "customer_id": {
                "table": "customers",
                "url": "/data/orders/-/foreign-key-suggestions?column=customer_id",
            }
        }
        # The clerk cannot view customers: no autocomplete offered, and the
        # page does not reveal the referenced table through this feature
        response = await ds.client.get("/data/orders", actor={"id": "clerk"})
        assert response.status_code == 200
        table_data = table_data_from_html(response.text)
        assert "foreignKeys" not in table_data
        # The clerk still gets the insert dialog data
        assert table_data["insertRow"]["tableName"] == "orders"
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_table_page_without_foreign_keys_has_no_foreign_keys_key():
    ds = make_ds()
    try:
        db = add_memory_db(ds)
        await db.execute_write_script(
            "create table plain (id integer primary key, name text);"
        )
        response = await ds.client.get("/data/plain")
        assert response.status_code == 200
        assert table_data_from_html(response.text) == {"tableUrl": "/data/plain"}
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_timeout_returns_ok_not_error():
    ds = make_ds(extra_config={"sql_time_limit_ms": 1})
    try:
        db = add_memory_db(ds)
        await db.execute_write_script("""
            create table customers (id integer primary key, name text);
            create table orders (
                id integer primary key,
                customer_id integer references customers(id)
            );
        """)
        await db.execute_write_fn(
            lambda conn: conn.executemany(
                "insert into customers (id, name) values (?, ?)",
                ((i, "Customer {}".format(i)) for i in range(1, 50_001)),
            )
        )
        # With a 1ms time limit the search query is interrupted - the
        # endpoint must degrade to an empty result, not an error
        response = await ds.client.get(suggestions_url(q="zzz_no_match"))
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["rows"] == []
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_table_page_insert_dialog_still_works_with_foreign_keys():
    # The insert dialog data is unchanged apart from the new foreignKeys key
    ds = await make_orders_ds(
        table_config={
            "orders": {"permissions": {"insert-row": {"id": "root"}}},
        }
    )
    try:
        response = await ds.client.get("/data/orders", actor={"id": "root"})
        assert response.status_code == 200
        table_data = table_data_from_html(response.text)
        assert table_data["insertRow"]["tableName"] == "orders"
        assert table_data["insertRow"]["path"] == "/data/orders/-/insert"
        assert table_data["foreignKeys"]["customer_id"]["table"] == "customers"
        soup = Soup(response.text, "html.parser")
        assert (
            soup.select_one('button[data-table-action="insert-row"]') is not None
        )
    finally:
        ds.close()
