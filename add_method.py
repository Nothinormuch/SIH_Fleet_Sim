#!/usr/bin/env python3
"""Add _at_planned_cell method before _replan in amr.py"""

with open('src/amr.py', 'r', newline='') as f:
    content = f.read()

# Find the pattern: self._route_start_t = t followed by blank line then def _replan
# We need to insert the new method between them

# First, let's find the _route_start_t assignment and _replan method
import re

# Look for the route_start_t assignment in the _route_loop method
# and insert _at_planned_cell before _replan

# Strategy: find "def _replan" and insert before it
lines = content.split('\n')

# Find line numbers
new_lines = []
inserted = False
for i, line in enumerate(lines):
    new_lines.append(line)
    # After line "self._route_start_t = t" (which is in _route_loop), 
    # and after the blank line, insert _at_planned_cell before def _replan
    if not inserted and line.strip() == 'def _replan(self, t: float, start: Cell) -> None:':
        # Insert the new method before this line
        at_planned = [
            '',
            '    def _at_planned_cell(self, cell: Cell) -> bool:',
            '        """Check whether robot\'s current cell matches the current waypoint in the route."""',
            '        if not self.path or self.pidx >= len(self.path):',
            '            return False',
            '        return cell == self.path[self.pidx]',
        ]
        new_lines.extend(at_planned)
        inserted = True
        print(f"Inserted _at_planned_cell before line {i+1}")

if not inserted:
    print("WARNING: Could not find _replan method to insert before")

with open('src/amr.py', 'w', newline='') as f:
    f.write('\n'.join(new_lines))
print("Done")