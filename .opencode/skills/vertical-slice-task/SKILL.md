---
name: vertical-slice-task
description: Break features into vertical slices (UI+API+DB together)
---
## Principle
Each task = one working vertical slice (UI + API + DB), not horizontal layers.
Example: "Add reorder level" = DB column + API PUT + UI input + test, not "DB then API then UI".