"""Small, dependency-free assignment algorithms used by the fleet manager."""

from __future__ import annotations

import math
from typing import Sequence


def hungarian(costs: Sequence[Sequence[float]]) -> list[tuple[int, int]]:
    """Return minimum-cost row/column pairs using the Hungarian algorithm.

    The matrix may be rectangular. When there are more rows than columns, the
    problem is transposed internally and the returned pairs are mapped back to the
    original matrix. Ties are resolved by input order, so assignments are stable for
    a deterministic list of robot and task IDs.
    """
    if not costs:
        return []

    rows = len(costs)
    cols = len(costs[0])
    if cols == 0:
        return []
    if any(len(row) != cols for row in costs):
        raise ValueError("cost matrix must be rectangular")

    matrix = [[float(value) for value in row] for row in costs]
    if any(not math.isfinite(value) for row in matrix for value in row):
        raise ValueError("cost matrix must contain only finite values")

    if rows > cols:
        transposed = [[matrix[i][j] for i in range(rows)] for j in range(cols)]
        return sorted((col, row) for row, col in hungarian(transposed))

    # This is the classic O(n^3) primal-dual implementation for n <= m.
    u = [0.0] * (rows + 1)
    v = [0.0] * (cols + 1)
    matched_col = [0] * (cols + 1)
    predecessor = [0] * (cols + 1)

    for row in range(1, rows + 1):
        matched_col[0] = row
        column = 0
        min_delta = [float("inf")] * (cols + 1)
        used = [False] * (cols + 1)

        while True:
            used[column] = True
            current_row = matched_col[column]
            delta = float("inf")
            next_column = 0
            for candidate in range(1, cols + 1):
                if used[candidate]:
                    continue
                reduced = (matrix[current_row - 1][candidate - 1]
                           - u[current_row] - v[candidate])
                if reduced < min_delta[candidate]:
                    min_delta[candidate] = reduced
                    predecessor[candidate] = column
                if min_delta[candidate] < delta:
                    delta = min_delta[candidate]
                    next_column = candidate

            for candidate in range(cols + 1):
                if used[candidate]:
                    u[matched_col[candidate]] += delta
                    v[candidate] -= delta
                else:
                    min_delta[candidate] -= delta

            column = next_column
            if matched_col[column] == 0:
                break

        while True:
            previous = predecessor[column]
            matched_col[column] = matched_col[previous]
            column = previous
            if column == 0:
                break

    pairs = [(matched_col[column] - 1, column - 1)
             for column in range(1, cols + 1) if matched_col[column]]
    return sorted(pairs)
