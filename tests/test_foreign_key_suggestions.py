from datasette.app import Datasette, Database
from datasette.database import QueryInterrupted
from bs4 import BeautifulSoup as Soup
import itertools
import json
import pytest
import pytest_asyncio
import re

SCHEMA = """
create table customers (
    id integer primary key,
    name text,
    code text
);
insert into customers (id, name, code) values
    (5, '100% Legit', 'pct'),
    (6, 'under_score Co', 'usc'),
    (7, 'Plain Customer', 'pln'),
    (12, 'Zebra Holdings', 'z12'),
    (112, 'Apple & Sons', 'a112'),
    (120, 'Avocado Inc', 'a120');

create table orders (
    id integer primary key,
    customer_id integer references customers(id),
    note text
);
insert into orders (id, customer_id, note) values (1, 12, 'first order');

create table things (
    id integer primary key,
    score real,
    quantity integer
);
insert into things (id, score, quantity) values (3, 1.5, 4);

create table gadgets (
    id integer primary key,
    thing_id integer references things(id)
);

create table tags (
    tag text primary key
);
insert into tags (tag) values ('rush order'), ('gift');

create table order_tags (
    id integer primary key,
    tag text references tags(tag)
);

create view orders_view as select * from orders;
"""

_memory_counter = itertools.count(1)


async def make_datasette(config=None):
    ds = Datasette([], config=config or {})
    db = ds.add_database(
        Database(ds, memory_name="fk_suggestions_{}".format(next(_memory_counter))),
        name="data",
    )
    await db.execute_write_script(SCHEMA)
    return ds


@pytest_asyncio.fixture
async def ds():
    datasette = await make_datasette()
    yield datasette
    datasette.close()


def table_data_from_html(html):
    soup = Soup(html, "html.parser")
    script = [
        s for s in soup.find_all("script") if "_datasetteTableData" in (s.string or "")
    ][0]
    match = re.search(
        r"window\._datasetteTableData\s*=\s*({.*?});",
        script.string,
        re.DOTALL,
    )
    return json.loads(match.group(1))


@pytest.mark.asyncio
async def test_suggestions_basic(ds):
    response = await ds.client.get(
        "/data/orders/-/foreign-key-suggestions?column=customer_id&q=apple"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["database"] == "data"
    assert data["table"] == "orders"
    assert data["column"] == "customer_id"
    assert data["query"] == "apple"
    assert data["other_table"] == "customers"
    assert data["other_column"] == "id"
    assert data["label_column"] == "name"
    assert data["truncated"] is False
    assert data["timed_out"] is False
    assert data["results"] == [
        {"value": 112, "label": "Apple & Sons", "url": "/data/customers/112"}
    ]
    # The raw value keeps its integer type
    assert isinstance(data["results"][0]["value"], int)


@pytest.mark.asyncio
async def test_suggestions_exact_match_sorted_first(ds):
    response = await ds.client.get(
        "/data/orders/-/foreign-key-suggestions?column=customer_id&q=12"
    )
    assert response.status_code == 200
    # 12 is an exact match, 112 and 120 only contain "12"
    assert [r["value"] for r in response.json()["results"]] == [12, 112, 120]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "q,expected_ids",
    (
        ("%", [5]),
        ("100%", [5]),
        ("_", [6]),
        ("under_score", [6]),
    ),
)
async def test_suggestions_like_wildcards_are_literal(ds, q, expected_ids):
    response = await ds.client.get(
        "/data/orders/-/foreign-key-suggestions?column=customer_id&q={}".format(q)
    )
    assert response.status_code == 200
    assert [r["value"] for r in response.json()["results"]] == expected_ids


@pytest.mark.asyncio
async def test_suggestions_uses_configured_label_column():
    ds = await make_datasette(
        config={
            "databases": {"data": {"tables": {"customers": {"label_column": "code"}}}}
        }
    )
    try:
        response = await ds.client.get(
            "/data/orders/-/foreign-key-suggestions?column=customer_id&q=z12"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["label_column"] == "code"
        assert data["results"] == [
            {"value": 12, "label": "z12", "url": "/data/customers/12"}
        ]
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_label_falls_back_to_value(ds):
    # things has no detectable label column
    response = await ds.client.get(
        "/data/gadgets/-/foreign-key-suggestions?column=thing_id&q=3"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["label_column"] is None
    assert data["results"] == [{"value": 3, "label": "3", "url": "/data/things/3"}]


@pytest.mark.asyncio
async def test_suggestions_empty_query_returns_first_rows(ds):
    response = await ds.client.get(
        "/data/orders/-/foreign-key-suggestions?column=customer_id"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == ""
    assert [r["value"] for r in data["results"]] == [5, 6, 7, 12, 112, 120]


@pytest.mark.asyncio
async def test_suggestions_truncated(ds):
    db = ds.get_database("data")
    await db.execute_write(
        "insert into customers (name) values {}".format(
            ", ".join("('Bulk {}')".format(i) for i in range(15))
        )
    )
    response = await ds.client.get(
        "/data/orders/-/foreign-key-suggestions?column=customer_id&q=bulk"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 10
    assert data["truncated"] is True


@pytest.mark.asyncio
async def test_suggestions_text_primary_key(ds):
    response = await ds.client.get(
        "/data/order_tags/-/foreign-key-suggestions?column=tag&q=rush order"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["other_column"] == "tag"
    assert data["results"][0] == {
        "value": "rush order",
        "label": "rush order",
        "url": "/data/tags/rush+order",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_status",
    (
        ("/data/orders/-/foreign-key-suggestions", 400),
        ("/data/orders/-/foreign-key-suggestions?column=note", 400),
        ("/data/orders/-/foreign-key-suggestions?column=id", 400),
        ("/data/missing/-/foreign-key-suggestions?column=customer_id", 404),
        ("/data/orders_view/-/foreign-key-suggestions?column=customer_id", 400),
    ),
)
async def test_suggestions_errors(ds, path, expected_status):
    response = await ds.client.get(path)
    assert response.status_code == expected_status
    assert response.json()["ok"] is False


@pytest.mark.asyncio
async def test_suggestions_timed_out_still_returns_exact_match(ds, monkeypatch):
    db = ds.get_database("data")
    original_execute = db.execute

    async def fake_execute(sql, params=None, **kwargs):
        if " like " in sql.lower():
            raise QueryInterrupted("interrupted", sql, params)
        return await original_execute(sql, params, **kwargs)

    monkeypatch.setattr(db, "execute", fake_execute)

    # Exact match still comes back even though the fuzzy search timed out
    response = await ds.client.get(
        "/data/orders/-/foreign-key-suggestions?column=customer_id&q=12"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["timed_out"] is True
    assert [r["value"] for r in data["results"]] == [12]

    # A query with no exact match returns an empty list, not an error
    response = await ds.client.get(
        "/data/orders/-/foreign-key-suggestions?column=customer_id&q=apple"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["timed_out"] is True
    assert data["results"] == []


RESTRICTED_CONFIG = {
    "databases": {
        "data": {
            "tables": {
                "customers": {
                    "permissions": {"view-table": {"id": "boss"}},
                },
                "orders": {
                    "permissions": {
                        "insert-row": {"id": "editor"},
                        "update-row": {"id": "editor"},
                    },
                },
            }
        }
    }
}


@pytest.mark.asyncio
async def test_suggestions_denied_if_referenced_table_not_visible():
    ds = await make_datasette(config=RESTRICTED_CONFIG)
    try:
        # The editor can insert/update orders but cannot view customers
        response = await ds.client.get(
            "/data/orders/-/foreign-key-suggestions?column=customer_id&q=zebra",
            actor={"id": "editor"},
        )
        assert response.status_code == 403
        assert "Zebra" not in response.text
        assert "customers" not in response.text

        # The boss can view customers and gets suggestions
        response = await ds.client.get(
            "/data/orders/-/foreign-key-suggestions?column=customer_id&q=zebra",
            actor={"id": "boss"},
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["label"] == "Zebra Holdings"
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_suggestions_denied_if_table_not_visible():
    config = {
        "databases": {
            "data": {
                "tables": {
                    "orders": {
                        "permissions": {"view-table": {"id": "boss"}},
                    },
                },
            }
        }
    }
    ds = await make_datasette(config=config)
    try:
        response = await ds.client.get(
            "/data/orders/-/foreign-key-suggestions?column=customer_id&q=zebra",
            actor={"id": "editor"},
        )
        assert response.status_code == 403
        response = await ds.client.get(
            "/data/orders/-/foreign-key-suggestions?column=customer_id&q=zebra",
            actor={"id": "boss"},
        )
        assert response.status_code == 200
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_table_page_includes_foreign_keys_metadata():
    ds = await make_datasette(
        config={
            "databases": {
                "data": {
                    "tables": {
                        "orders": {
                            "permissions": {
                                "insert-row": {"id": "editor"},
                                "update-row": {"id": "editor"},
                            },
                        },
                    },
                }
            }
        }
    )
    try:
        response = await ds.client.get("/data/orders", actor={"id": "editor"})
        assert response.status_code == 200
        table_data = table_data_from_html(response.text)
        assert "insertRow" in table_data
        assert table_data["foreignKeys"] == {
            "customer_id": {
                "table": "customers",
                "column": "id",
                "url": "/data/orders/-/foreign-key-suggestions?column=customer_id",
            }
        }
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_table_page_foreign_keys_for_update_only_actor():
    ds = await make_datasette(
        config={
            "databases": {
                "data": {
                    "tables": {
                        "orders": {
                            "permissions": {
                                "update-row": {"id": "updater"},
                            },
                        },
                    },
                }
            }
        }
    )
    try:
        response = await ds.client.get("/data/orders", actor={"id": "updater"})
        assert response.status_code == 200
        table_data = table_data_from_html(response.text)
        # No insert-row permission, so no insert dialog data
        assert "insertRow" not in table_data
        # ... but the edit dialog still gets the foreign key metadata
        assert table_data["foreignKeys"] == {
            "customer_id": {
                "table": "customers",
                "column": "id",
                "url": "/data/orders/-/foreign-key-suggestions?column=customer_id",
            }
        }
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_table_page_hides_foreign_keys_if_referenced_table_not_visible():
    ds = await make_datasette(config=RESTRICTED_CONFIG)
    try:
        response = await ds.client.get("/data/orders", actor={"id": "editor"})
        assert response.status_code == 200
        table_data = table_data_from_html(response.text)
        assert "insertRow" in table_data
        # customers is not visible to this actor, so no suggestions are offered
        assert "foreignKeys" not in table_data
        assert "foreign-key-suggestions" not in response.text
        assert "Zebra" not in response.text
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_table_page_no_foreign_keys_metadata_without_write_permission(ds):
    response = await ds.client.get("/data/orders")
    assert response.status_code == 200
    table_data = table_data_from_html(response.text)
    assert table_data == {"tableUrl": "/data/orders"}
