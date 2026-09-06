# Schema 1.0

Each graph has an ID, semantic version, and 1-200 tasks. A task declares owner, description, isolated relative path scope, dependencies, 1-10 attempts, and one or more evidence kinds. Unknown fields, cycles, missing dependencies, duplicate IDs, and path ownership conflicts fail validation.

