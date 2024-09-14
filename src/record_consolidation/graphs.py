from collections import defaultdict
from itertools import combinations
from typing import Any, Literal

import networkx as nx
import polars as pl

from .utils import extract_connected_subgraphs


def unconsolidated_df_to_graph(
    df: pl.DataFrame, weight_edges: bool = False
) -> nx.Graph:
    """
    Converts an unconsolidated Polars DataFrame into a NetworkX graph.

    This function processes a DataFrame where each row represents an entity with various attributes (e.g., a company with a name, CUSIP, ISIN, etc.).
    It creates a graph where each unique attribute value becomes a node, and edges are formed between
    nodes that appear together in the same row. Optionally, edges can be weighted based on the number
    of times the connected nodes co-occur.

    Args:
        df (pl.DataFrame): The input DataFrame containing entity attributes.
        weight_edges (bool): If True, edges will be weighted based on co-occurrence counts. Defaults to False.

    Returns:
        nx.Graph: A NetworkX graph where nodes represent unique attribute values and edges represent
                  co-occurrences of these values within the same row of the DataFrame.
    """
    df = df.with_columns(pl.col(pl.String).replace(["", "N/A"], None))
    G: nx.Graph = nx.Graph()
    for row in df.rows(named=True):
        row = {k: v for k, v in row.items() if v is not None}
        seen_values = set()  # Track values seen in this row

        # Increment node counts for each unique value in the row
        for field, value in row.items():
            if value not in seen_values:
                if value not in G.nodes:
                    G.add_node(value, field=field, count=1)
                else:
                    G.nodes[value]["count"] += 1
                seen_values.add(value)  # Mark this value as seen in this row

        # Add edges between combinations of values
        for (field1, value1), (field2, value2) in combinations(row.items(), r=2):
            if weight_edges:
                if G.has_edge(value1, value2):
                    G[value1][value2]["count"] += 1
                else:
                    G.add_edge(value1, value2, count=1, fields={field1, field2})
            else:
                G.add_edge(value1, value2)
    return G


def _extract_canonicals_from_subgraph(
    g: nx.Graph,
    method: Literal["max_n"],  # others could be graph algs like page-rank, etc.
) -> dict[str, Any]:
    """
    Given a connected sub-graph, this function returns a dict of field_name:canonical_value.
    Requirements:
        1) The sub-graph's nodes all have a `field` attribute that classifies them.
        2) The sub-graph's nodes all have a `count` attribute that counts the number of times they've been observed.
        # 3) The sub-graph's edges all have a `count` attribute that counts the number of times they've been observed.

    Args:
        g (nx.Graph): Connected sub-graph
        method: Method for determining canonicals

    Returns:
        dict[str, Any]: dict of field name : canonical value
    """
    canonicals: dict[str, Any] = dict()
    fields: set[str] = {v["field"] for v in g.nodes.values()}

    match method:
        case "max_n":
            for field in fields:
                respective_nodes = [n for n in g.nodes if g.nodes[n]["field"] == field]
                max_n_node = max(respective_nodes, key=lambda x: g.nodes[x]["count"])
                canonicals[field] = max_n_node
        case _:
            raise ValueError(method)

    return canonicals


def _extract_consolidation_mapping_from_subgraph(
    g: nx.Graph,
) -> dict[str, dict[str, Any]]:
    """
    Extracts a mapping of nodes from a subgraph, grouping them by their respective fields and mapping each
    node to a canonical value.

    This function creates a mapping for each field present in the graph. It associates every node within
    the same field to a canonical value, which is retrieved using the `_extract_canonicals_from_subgraph` function.
    If there are duplicate nodes with the same value within a field, a `ValueError` is raised.

    Args:
        g (nx.Graph): A networkx graph where each node contains a "field" attribute.

    Returns:
        dict[str, dict[str, Any]]: A nested dictionary where the outer keys are field names, and the inner
                                   dictionary maps each node to a canonical value corresponding to that field.

    Raises:
        ValueError: If a node appears more than once in a field, indicating duplicate values in the field.

    Example:
        If the graph `g` has nodes with different CUSIP values, this function will map each CUSIP node
        to the same canonical CUSIP value.
    """
    mapping: dict[str, dict[str, Any]] = dict()
    canonicals: dict[str, Any] = _extract_canonicals_from_subgraph(g, "max_n")
    fields: set[str] = {v["field"] for v in g.nodes.values()}

    for field in fields:
        mapping_for_this_field: dict[str, Any] = dict()
        respective_nodes = [n for n in g.nodes if g.nodes[n]["field"] == field]

        for node in respective_nodes:
            if node in mapping_for_this_field:
                raise ValueError(
                    f"Each node should represent a unique value (e.g., 'US5949181045').\
                          Since {node=} is already in `mapping_for_this_field.keys()`, \
                            there must be another node with the same value.\
                                \n{mapping_for_this_field=}\n{field=}",
                )

            mapping_for_this_field[node] = canonicals[
                field
            ]  # we're mapping all values in the field to the same canonical. e.g., all CUSIP values are mapped to the canonical CUSIP value of "123".

        mapping[field] = mapping_for_this_field

    return mapping


def extract_consolidation_mapping_from_graph(g: nx.Graph) -> dict[str, dict[str, Any]]:
    """
    Extracts a consolidation mapping from a graph by processing its connected subgraphs.

    This function iterates over each connected subgraph in the input graph `g`, extracts a consolidation
    mapping for each subgraph, and combines these mappings into an overall consolidation mapping. The
    consolidation mapping groups nodes by their respective fields and maps each node to a canonical value.

    Args:
        g (nx.Graph): A networkx graph where each node contains a "field" attribute.

    Returns:
        dict[str, dict[str, Any]]: A nested dictionary where the outer keys are field names, and the inner
                                   dictionary maps each node to a canonical value corresponding to that field.
    """
    overall_consolidations: dict[str, dict[str, Any]] = defaultdict(dict)
    for subg in extract_connected_subgraphs(g):
        consolidation_mapping = _extract_consolidation_mapping_from_subgraph(subg)
        # e.g.,
        # {'cusip': {'594918104': '594918104', '594918105': '594918104'},
        # 'figi': {'MSFT': 'MSFT'},
        # 'isin': {'US5949181045': 'US5949181045'},
        # 'issuer_name': {'MICROSOFT CORPORATION': 'MICROSOFT CORPORATION'}
        for field, field_mapping in consolidation_mapping.items():
            # TODO: check for pre-existing overwrites? should be basically impossible but you never know
            # e.g. ("cusip", {'594918104': '594918104', '594918105': '594918104'})
            overall_consolidations[field].update(field_mapping)
    return overall_consolidations


def _create_field_val_to_canonical_lookup(
    df: pl.DataFrame,
) -> dict[str, dict[Any, dict[str, Any]]]:
    """
    Creates a lookup dictionary mapping each field value to its canonical values across all fields.

    This function processes an input DataFrame to generate a NetworkX graph where nodes represent unique
    attribute values and edges represent co-occurrences of these values within the same row. It then extracts
    connected subgraphs from this graph and determines the canonical value for each field within these subgraphs.
    Finally, it constructs a nested dictionary where each field maps to another dictionary that maps each value
    to its canonical values across all fields.

    Args:
        df (pl.DataFrame): The input DataFrame containing entity attributes.

    Returns:
        dict[str, dict[Any, dict[str, Any]]]: A nested dictionary where the outer keys are field names, the
                                              middle keys are field values, and the inner dictionary maps
                                              each field value to its canonical values across all fields.

    Example:
        Input DataFrame:
            ┌───────────────────────┬────────────┬───────────────┬───────────────────┐
            │ issuer_name           ┆ cusip      ┆ isin          ┆ figi              │
            ├───────────────────────┼────────────┼───────────────┼───────────────────┤
            │ MICROSOFT CORPORATION ┆ 594918104  ┆ US5949181045  ┆ MSFT              │
            │ MICROSOFT CORP        ┆ 594918105  ┆ US5949181055  ┆ MSFT              │
            │ APPLE INC             ┆ 037833100  ┆ US0378331005  ┆ AAPL              │
            └───────────────────────┴────────────┴───────────────┴───────────────────┘

        Output:
        {
            'issuer_name': {
                'MICROSOFT CORPORATION': {
                    'issuer_name': 'MICROSOFT CORPORATION',
                    'cusip': '594918104',
                    'isin': 'US5949181045',
                    'figi': 'MSFT'
                },
                ...,
            },
            'cusip': {
                '594918104': {
                    'issuer_name': 'MICROSOFT CORPORATION',
                    'cusip': '594918104',
                    'isin': 'US5949181045',
                    'figi': 'MSFT'
                },
                ...,
            },
            'isin': {
                'US5949181045': {
                    'issuer_name': 'MICROSOFT CORPORATION',
                    'cusip': '594918104',
                    'isin': 'US5949181045',
                    'figi': 'MSFT'
                },
                ...,
            },
            'figi': {
                'MSFT': {
                    'issuer_name': 'MICROSOFT CORPORATION',
                    'cusip': '594918104',
                    'isin': 'US5949181045',
                    'figi': 'MSFT'
                },
                ...,
            }
        }
    """
    mapping: dict[
        str,  # field, e.g., "issuer_name"
        dict[
            Any,  # value, e.g., "MICROSOFT CORPORATION"
            dict[  # canonical vals for this field-value pair (e.g., `{'figi': 'MSFT', 'issuer_name': 'MICROSOFT CORPORATION', 'cusip': '594918104', 'isin': 'US5949181045'}`)
                str,  # field name
                Any,  # canonical value
            ],
        ],
    ] = {field: dict() for field in df.columns}
    G: nx.Graph = unconsolidated_df_to_graph(df)
    for subg in extract_connected_subgraphs(G):
        canonical: dict[str, Any] = _extract_canonicals_from_subgraph(subg, "max_n")
        for value, field_and_count in G.nodes(data=True):
            # e.g., `('MICROSOFT CORPORATION', {'field': 'issuer_name', 'count': 6}`
            field: str = field_and_count["field"]
            if value not in mapping[field]:
                mapping[field][value] = canonical
    return mapping
