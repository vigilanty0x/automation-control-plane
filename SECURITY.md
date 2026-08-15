# Security policy

## Reporting

Use GitHub private vulnerability reporting for this repository. Do not attach real credentials, proprietary prompts, customer records, or production logs.

## Trust boundary

This release is a local routing reference implementation, not a production authorization service. JSON examples are synthetic. The router does not execute prompts, contact models, create branches, or mutate repositories.

Production adopters should additionally:

- authenticate every caller and worker;
- authorize capabilities and permissions from a server-controlled source;
- sign or independently verify completion evidence;
- encrypt sensitive SQLite files and backups;
- sanitize task titles, scopes, event reasons, and artifact paths before display;
- set database filesystem permissions and retention limits;
- rate-limit enqueue, approval, retry, and recovery operations;
- use an external durable queue when one host is insufficient;
- never treat the historical success field as a security decision by itself.

The CLI limits individual JSON inputs to 1 MB but does not sandbox file paths supplied by its operator.
